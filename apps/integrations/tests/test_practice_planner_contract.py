from dataclasses import FrozenInstanceError
from unittest.mock import Mock
from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, override_settings

from apps.integrations.practice_planner.contracts import (
    Category, ContentNotFound, DiagnosisQuery, PlannerUnavailable,
)
from apps.integrations.practice_planner.mock import MockPracticePlannerProvider
from apps.integrations.practice_planner.service import (
    WileyPracticePlannerService, get_practice_planner_service, service_for_user,
)


class PlannerContractTests(SimpleTestCase):
    def setUp(self):
        self.provider = MockPracticePlannerProvider()
        self.service = WileyPracticePlannerService(self.provider)
        self.options = {'edition': 'test-1'}

    def test_complete_paginated_hierarchy_without_credentials(self):
        query = DiagnosisQuery('SYNTHETIC', 'test', 'SYNTHETIC-001')
        page = self.service.lookup_diagnosis(query, **self.options)
        self.assertEqual(page.items[0].category, Category.PROBLEM)
        self.assertIsNotNone(page.next_cursor)
        second = self.service.lookup_diagnosis(query, cursor=page.next_cursor, **self.options)
        self.assertEqual(second.items[0].component_id, 'p2')
        self.assertIsNone(second.next_cursor)
        parent = page.items[0]
        for category in (Category.GOAL, Category.OBJECTIVE, Category.INTERVENTION):
            child = self.service.children(parent.component_id, **self.options).items[0]
            self.assertEqual(child.category, category)
            self.assertIn(parent.component_id, child.parent_ids)
            parent = child

    def test_note_and_homework_prompts_are_found_across_pages(self):
        self.assertEqual(self.service.progress_notes('p1', **self.options)[0].category, Category.PROGRESS_NOTE)
        self.assertEqual(self.service.homework('p1', **self.options)[0].category, Category.HOMEWORK)

    def test_versions_retirement_reuse_and_frozen_dtos(self):
        old = self.service.get_component('i1', edition='test-1')
        new = self.service.get_component('i1', edition='test-2')
        self.assertNotEqual(old.text, new.text)
        self.assertTrue(self.service.get_component('i2', edition='test-2').retired)
        self.assertFalse(self.service.get_component('i2', edition='test-1').retired)
        self.assertEqual(self.service.get_component('g1', **self.options).parent_ids, ('p1', 'p2'))
        with self.assertRaises(FrozenInstanceError):
            old.text = 'Changed'

    def test_explicit_diagnosis_system_and_no_inferred_crosswalk(self):
        icd = self.service.lookup_diagnosis(DiagnosisQuery('ICD-10-CM', 'test', 'TEST-001'), **self.options)
        dsm = self.service.lookup_diagnosis(DiagnosisQuery('DSM-5', 'test', 'TEST-001'), **self.options)
        self.assertTrue(icd.items)
        self.assertEqual(dsm.items, ())
        self.assertEqual(self.service.lookup_diagnosis(DiagnosisQuery('SYNTHETIC', 'test', 'UNKNOWN'), **self.options).items, ())
        with self.assertRaises(ValueError):
            DiagnosisQuery('', 'test', 'TEST-001')

    def test_missing_content_and_bad_pagination_fail_safely(self):
        for options in ({'edition': 'missing'}, {'edition': 'test-1', 'language': 'missing'}):
            with self.assertRaises(ContentNotFound):
                self.service.get_component('p1', **options)
        with self.assertRaises(ContentNotFound):
            self.service.children('missing', **self.options)
        for cursor in ('bad', '-1', '900'):
            with self.assertRaises(ContentNotFound):
                self.service.children('p1', cursor=cursor, **self.options)

    def test_provider_outage_does_not_return_made_up_content(self):
        provider = Mock()
        provider.get_component.side_effect = PlannerUnavailable('Provider unavailable')
        with self.assertRaises(PlannerUnavailable):
            WileyPracticePlannerService(provider).get_component('p1', **self.options)

    def test_disabled_default_and_production_fail_closed(self):
        for provider in ('disabled', 'mock', 'live', 'unknown'):
            with self.subTest(provider=provider), override_settings(DEBUG=False, PRACTICE_PLANNER_PROVIDER=provider):
                with self.assertRaises(PlannerUnavailable):
                    get_practice_planner_service()

    @override_settings(DEBUG=True, PRACTICE_PLANNER_PROVIDER='mock')
    def test_debug_mock_does_not_authorize_unverified_callers(self):
        self.assertTrue(get_practice_planner_service().get_component('p1', **self.options))
        for role in ('admin', 'supervisor', 'staff', 'caregiver', 'superadmin', None):
            with self.subTest(role=role), self.assertRaises(PermissionDenied):
                service_for_user(Mock(role=role))
