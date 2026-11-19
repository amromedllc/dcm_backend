"""Stage 6 live adapter: everything mocked, no real network/Wiley calls."""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.integrations.practice_planner.contracts import Category, ContentNotFound, DiagnosisQuery, PlannerUnavailable
from apps.integrations.practice_planner.live_provider import TP_FAMILY_MAP, WileyLiveProvider, fetch_homework_pdf
from apps.integrations.practice_planner.wiley_client import WileyApiError


@override_settings(WILEY_API_KEY='synthetic-test-key', WILEY_API_BASE_URL='https://wiley.test')
class WileyLiveProviderTests(SimpleTestCase):
    def setUp(self):
        self.provider = WileyLiveProvider()
        # Rate limiting is best-effort via Redis; fail-open if unavailable in tests.
        patcher = patch('apps.integrations.practice_planner.wiley_client._redis', side_effect=Exception('no redis in tests'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_lookup_diagnosis_searches_and_filters_to_requested_db(self):
        payload = {'results': [
            {'db': 'Adult_6e_TP', 'db_title': 'Adult Psychotherapy Treatment Planner, 6th Edition', 'problem_number': 3, 'description': 'Anxiety problem'},
            {'db': 'Child_6e_TP', 'db_title': 'Child Treatment Planner', 'problem_number': 9, 'description': 'Other family anxiety problem'},
        ]}
        with patch('apps.integrations.practice_planner.live_provider.fetch_json', return_value=payload) as fetch:
            page = self.provider.lookup_diagnosis(DiagnosisQuery('ICD-10-CM', 'test', 'anxiety'), edition='Adult_6e_TP')
        fetch.assert_called_once_with('/v1/treatment_planners/search', params={'q': 'anxiety', 'limit': 50}, bucket='search')
        self.assertEqual(len(page.items), 1)
        self.assertEqual(page.items[0].component_id, 'problem:3')
        self.assertEqual(page.items[0].category, Category.PROBLEM)
        self.assertIsNone(page.next_cursor)

    def test_lookup_diagnosis_rejects_pagination_and_unknown_db(self):
        with self.assertRaises(ContentNotFound):
            self.provider.lookup_diagnosis(DiagnosisQuery('SYNTHETIC', 'test', 'anxiety'), edition='Adult_6e_TP', cursor='1')
        with self.assertRaises(ContentNotFound):
            self.provider.lookup_diagnosis(DiagnosisQuery('SYNTHETIC', 'test', 'anxiety'), edition='Not_A_Real_Db')

    def test_get_component_returns_problem_description(self):
        payload = {'problem': {'ProblemNumber': 3, 'Description': 'Synthetic problem text'}}
        with patch('apps.integrations.practice_planner.live_provider.fetch_json', return_value=payload):
            content = self.provider.get_component('problem:3', edition='Adult_6e_TP')
        self.assertEqual(content.text, 'Synthetic problem text')
        self.assertEqual(content.provider, 'wiley')
        self.assertEqual(content.edition, 'Adult_6e_TP')
        self.assertEqual(content.parent_ids, ())

    def test_get_component_rejects_goal_objective_and_intervention_references(self):
        for component_id in ('goal:3:0', 'objective:3:1', 'intervention:3:1:1'):
            with self.assertRaises(ContentNotFound):
                self.provider.get_component(component_id, edition='Adult_6e_TP')

    def test_get_component_translates_404_to_content_not_found(self):
        with patch('apps.integrations.practice_planner.live_provider.fetch_json', side_effect=WileyApiError('missing', status_code=404)):
            with self.assertRaises(ContentNotFound):
                self.provider.get_component('problem:999', edition='Adult_6e_TP')

    def test_rejected_credentials_become_planner_unavailable_not_a_raw_500(self):
        # Regression: a bare `raise` previously let WileyApiError escape past
        # @safe_errors, which only catches PlannerError subclasses, producing a
        # raw 500 instead of a clean 503.
        with patch('apps.integrations.practice_planner.live_provider.fetch_json', side_effect=WileyApiError('bad key', status_code=403)):
            with self.assertRaises(PlannerUnavailable):
                self.provider.get_component('problem:3', edition='Adult_6e_TP')
        with patch('apps.integrations.practice_planner.live_provider.fetch_json', side_effect=WileyApiError('server error', status_code=500)):
            with self.assertRaises(PlannerUnavailable):
                self.provider.get_component('problem:3', edition='Adult_6e_TP')

    def test_children_returns_progress_notes_and_homework_only_for_mapped_family(self):
        problem_payload = {'problem': {'ProblemNumber': 3, 'Description': 'x'}}
        progress_payload = {'presentation_groups': [{'group_number': 1, 'notes': ['Describe the presentation.']}],
                             'interventions': [{'intervention_number': 2, 'notes': ['Describe intervention 2 progress.']}]}
        homework_payload = {'homework': [{'homework_number': 5, 'title': 'Practice at home', 'suggestions': 'Daily', 'is_primary': True}]}

        def fake_fetch(path, **kwargs):
            if path.startswith('/v1/treatment_planners/problem/'):
                return problem_payload
            if path.startswith('/v1/treatment_planners/progress_note/'):
                return progress_payload
            if path.startswith('/v1/treatment_planners/homework/'):
                return homework_payload
            raise AssertionError(f'unexpected path {path}')

        with patch('apps.integrations.practice_planner.live_provider.fetch_json', side_effect=fake_fetch):
            page = self.provider.children('problem:3', edition='Adult_6e_TP')

        categories = sorted(item.category for item in page.items)
        self.assertEqual(categories, sorted([Category.PROGRESS_NOTE, Category.PROGRESS_NOTE, Category.HOMEWORK]))
        self.assertTrue(all(item.parent_ids == ('problem:3',) for item in page.items))
        homework_item = next(item for item in page.items if item.category == Category.HOMEWORK)
        self.assertIn('Practice at home', homework_item.text)
        self.assertIn('Daily', homework_item.text)

    def test_children_omits_progress_notes_and_homework_for_unmapped_family(self):
        with patch('apps.integrations.practice_planner.live_provider.fetch_json', return_value={'problem': {'Description': 'x'}}) as fetch:
            page = self.provider.children('problem:1', edition='BehavioralMed')
        self.assertEqual(page.items, ())
        fetch.assert_called_once()  # only the problem-existence check; no progress_note/homework calls attempted

    def test_children_rejects_pagination(self):
        with self.assertRaises(ContentNotFound):
            self.provider.children('problem:3', edition='Adult_6e_TP', cursor='1')

    def test_family_map_only_contains_exact_prefix_pairs(self):
        # Regression guard: catches an accidental addition of an ambiguous pairing.
        for tp_db, siblings in TP_FAMILY_MAP.items():
            for sibling_db in siblings.values():
                self.assertTrue(sibling_db.startswith(tp_db.replace('_TP', '')) or tp_db.replace('_TP', '') in sibling_db,
                                 f'{tp_db} -> {sibling_db} is not an exact-prefix pairing')

    def test_fetch_homework_pdf_translates_404(self):
        with patch('apps.integrations.practice_planner.live_provider.fetch_pdf', side_effect=WileyApiError('missing', status_code=404)):
            with self.assertRaises(ContentNotFound):
                fetch_homework_pdf('Adult_6e_HW', 999)

    def test_fetch_homework_pdf_returns_bytes(self):
        with patch('apps.integrations.practice_planner.live_provider.fetch_pdf', return_value=b'%PDF-synthetic') as fetch:
            result = fetch_homework_pdf('Adult_6e_HW', 3)
        self.assertEqual(result, b'%PDF-synthetic')
        fetch.assert_called_once_with('/v1/treatment_planners/homework/Adult_6e_HW/file/3')


class WileyClientRateLimitTests(SimpleTestCase):
    @override_settings(WILEY_API_KEY='synthetic-test-key', WILEY_API_BASE_URL='https://wiley.test')
    def test_exceeding_the_configured_rate_limit_raises(self):
        from apps.integrations.practice_planner.wiley_client import _enforce_rate_limit

        class FakeRedis:
            def __init__(self):
                self.count = 0

            def incr(self, _key):
                self.count += 1
                return self.count

            def expire(self, *_args):
                pass

        fake = FakeRedis()
        with patch('apps.integrations.practice_planner.wiley_client._redis', return_value=fake):
            for _ in range(10):
                _enforce_rate_limit('search')
            with self.assertRaises(WileyApiError):
                _enforce_rate_limit('search')

    def test_missing_api_key_raises_before_any_request(self):
        from apps.integrations.practice_planner.wiley_client import fetch_json
        with override_settings(WILEY_API_KEY=''):
            with patch('apps.integrations.practice_planner.wiley_client._redis', side_effect=Exception('no redis')):
                with self.assertRaises(WileyApiError):
                    fetch_json('/v1/treatment_planners/ping')
