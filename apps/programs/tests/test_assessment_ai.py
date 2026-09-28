"""AI-drafted assessments: output clean-up, the AI client, and the two endpoints (AI call mocked)."""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.programs import api as programs_api
from apps.programs.assessment_draft import (
    MAX_AREAS, MAX_SKILLS_PER_AREA, SYSTEM_PROMPT, generate_assessment_draft, normalize_assessment_draft,
)
from apps.programs.models import Program, ProgramModule, Target
from apps.programs.schemas import AssessmentDraftAreaSchema, AssessmentDraftRequest, AssessmentDraftSchema
from apps.tenants.models import Organization
from shared import ai_client
from shared.ai_client import AIError
from shared.tenancy import tenant_context


class DraftCleaningTests(SimpleTestCase):
    def test_trims_dedupes_strips_html_and_caps(self):
        raw = {
            'name': '  <b>Early Skills</b> ',
            'objective': 'Find gaps.\n\n',
            'areas': [
                {'name': 'Language', 'skills': ['Asks for items', 'asks for items', '  ', '<i>Names items</i>']},
                {'name': '', 'skills': ['orphan']},
                {'name': 'Empty area', 'skills': []},
                'not a dict',
            ] + [{'name': f'Area {i}', 'skills': ['x']} for i in range(30)],
        }
        clean = normalize_assessment_draft(raw)
        self.assertEqual(clean['name'], 'Early Skills')
        self.assertEqual(clean['areas'][0], {'name': 'Language', 'skills': ['Asks for items', 'Names items']})
        self.assertLessEqual(len(clean['areas']), MAX_AREAS)

    def test_skill_count_is_capped(self):
        clean = normalize_assessment_draft({'areas': [{'name': 'A', 'skills': [f'skill {i}' for i in range(100)]}]})
        self.assertEqual(len(clean['areas'][0]['skills']), MAX_SKILLS_PER_AREA)
        self.assertEqual(clean['name'], 'Skills Assessment (AI draft)')

    def test_nothing_usable_is_an_error(self):
        with self.assertRaises(AIError):
            normalize_assessment_draft({'areas': [{'name': 'A', 'skills': []}]})

    def test_prompt_forbids_copying_published_assessments(self):
        self.assertIn('VB-MAPP', SYSTEM_PROMPT)
        self.assertIn('Do NOT copy', SYSTEM_PROMPT)

    def test_short_description_is_rejected_before_calling_the_ai(self):
        with mock.patch('apps.programs.assessment_draft.chat_json') as call:
            with self.assertRaises(AIError) as raised:
                generate_assessment_draft('hi')
        call.assert_not_called()
        self.assertEqual(raised.exception.status, 400)


class AIClientTests(SimpleTestCase):
    def test_extracts_json_from_fences_and_prose(self):
        self.assertEqual(ai_client.extract_json('Sure!\n```json\n{"a": 1}\n```\nDone'), {'a': 1})
        with self.assertRaises(AIError):
            ai_client.extract_json('no json here')

    @override_settings(AI_API_KEY='')
    def test_not_configured_is_a_503(self):
        with self.assertRaises(AIError) as raised:
            ai_client.chat_json('s', 'u')
        self.assertEqual(raised.exception.status, 503)

    @override_settings(AI_API_KEY='nvapi-test', AI_API_BASE_URL='https://example.test/v1', AI_MODEL='m', AI_TIMEOUT_SECONDS=5)
    def test_sends_an_openai_style_request_and_parses_the_reply(self):
        reply = mock.Mock(ok=True, status_code=200)
        reply.json.return_value = {'choices': [{'message': {'content': '{"areas": []}'}}]}
        with mock.patch('shared.ai_client.requests.post', return_value=reply) as post:
            result = ai_client.chat_json('system text', 'user text')
        self.assertEqual(result, {'areas': []})
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://example.test/v1/chat/completions')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer nvapi-test')
        self.assertEqual(kwargs['json']['model'], 'm')
        self.assertEqual([m['role'] for m in kwargs['json']['messages']], ['system', 'user'])

    @override_settings(AI_API_KEY='nvapi-test')
    def test_provider_errors_map_to_friendly_statuses(self):
        for status_code, expected in ((429, 429), (500, 502), (401, 502), (404, 502), (410, 502)):
            with self.subTest(status_code):
                reply = mock.Mock(ok=status_code < 400, status_code=status_code, text='provider said no')
                with mock.patch('shared.ai_client.requests.post', return_value=reply):
                    with self.assertRaises(AIError) as raised:
                        ai_client.chat_json('s', 'u')
                self.assertEqual(raised.exception.status, expected)


class AssessmentDraftEndpointTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='AI Org', slug='ai-org', schema_name='ai_org')
        self.admin = User.objects.create_user(
            email='ai-admin@example.com', password='x', organization=self.org, role=User.Role.ADMIN,
        )
        self.staff = User.objects.create_user(
            email='ai-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )

    def _request(self, user):
        return SimpleNamespace(user=user, tenant=self.org)

    def test_draft_returns_a_cleaned_draft_and_saves_nothing(self):
        model_reply = {'name': 'Language check', 'objective': 'Find gaps.',
                       'areas': [{'name': 'Requesting', 'skills': ['Asks for a snack']}]}
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch('apps.programs.assessment_draft.chat_json', return_value=model_reply):
                draft = programs_api.draft_assessment_with_ai(
                    self._request(self.admin), AssessmentDraftRequest(description='early language for toddlers'),
                )
            saved = Program.objects.count()
        self.assertEqual(draft['areas'][0]['skills'], ['Asks for a snack'])
        self.assertEqual(saved, 0)

    def test_ai_failure_becomes_an_http_error_with_its_status(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch('apps.programs.assessment_draft.chat_json', side_effect=AIError('busy', status=429)):
                with self.assertRaises(HttpError) as raised:
                    programs_api.draft_assessment_with_ai(
                        self._request(self.admin), AssessmentDraftRequest(description='early language for toddlers'),
                    )
        self.assertEqual(raised.exception.status_code, 429)

    def test_staff_cannot_draft_or_save(self):
        draft = AssessmentDraftSchema(name='x', areas=[AssessmentDraftAreaSchema(name='A', skills=['s'])])
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch('apps.programs.assessment_draft.chat_json') as call:
                with self.assertRaises(HttpError) as first:
                    programs_api.draft_assessment_with_ai(
                        self._request(self.staff), AssessmentDraftRequest(description='early language for toddlers'),
                    )
                with self.assertRaises(HttpError) as second:
                    programs_api.create_assessment_from_draft(self._request(self.staff), draft)
        call.assert_not_called()
        self.assertEqual((first.exception.status_code, second.exception.status_code), (403, 403))

    def test_saving_a_draft_creates_an_assessment_program(self):
        draft = AssessmentDraftSchema(
            name='Toddler language', objective='Find gaps.',
            areas=[
                AssessmentDraftAreaSchema(name='Requesting', skills=['Asks for a snack', 'Asks for help']),
                AssessmentDraftAreaSchema(name='Naming', skills=['Names a ball']),
            ],
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            code, program = programs_api.create_assessment_from_draft(self._request(self.admin), draft)
            row = Program.objects.get(pk=program['id'])
            modules = list(ProgramModule.objects.filter(program=row).values_list('name', flat=True))
            skills = list(Target.objects.filter(program=row).order_by('display_order').values_list('name', flat=True))
        self.assertEqual(code, 201)
        self.assertEqual((row.category, row.is_template, row.name), ('assessment', True, 'Toddler language'))
        self.assertEqual(modules, ['Requesting', 'Naming'])
        self.assertEqual(skills, ['Asks for a snack', 'Asks for help', 'Names a ball'])
        self.assertIsNotNone(row.prompting_template_id)

    @override_settings(AI_API_KEY='nvapi-test', AI_MODEL='no/such-model')
    def test_missing_or_retired_model_names_the_setting_to_fix(self):
        for status_code in (404, 410):
            reply = mock.Mock(ok=False, status_code=status_code, text='gone')
            with mock.patch('shared.ai_client.requests.post', return_value=reply):
                with self.assertRaises(AIError) as raised:
                    ai_client.chat_json('s', 'u')
            self.assertIn('AI_MODEL', str(raised.exception))
            self.assertIn('no/such-model', str(raised.exception))
