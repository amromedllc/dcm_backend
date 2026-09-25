"""AI-drafted programs: only values DCM accepts survive, and the endpoint respects permissions."""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import RolePermission, User
from apps.programs import api as programs_api
from apps.programs.models import ProgramTag, TreatmentArea
from apps.programs.program_draft import (
    MAX_TARGETS, SYSTEM_PROMPT_TEMPLATE, generate_program_draft, normalize_program_draft,
)
from apps.programs.schemas import ProgramDraftRequest
from apps.tenants.models import Organization
from shared.ai_client import AIError
from shared.tenancy import tenant_context

AREAS = ['Communication', 'Social Skills']
TAGS = ['Priority', 'Parent Training']


class ProgramDraftCleaningTests(SimpleTestCase):
    def test_keeps_only_values_dcm_accepts(self):
        clean = normalize_program_draft({
            'name': ' <b>Requesting items</b> ',
            'category': 'made_up',
            'treatment_area': 'communication',
            'tags': ['priority', 'Invented', 'PRIORITY'],
            'objective': 'Learner asks for items.',
            'instructions': 'Hold up the item\n<script>x</script>Wait 5 seconds\n\n',
            'targets': [
                {'name': 'Asks for snack', 'measurement_type': 'discrete_trial'},
                {'name': 'asks for snack', 'measurement_type': 'duration'},
                {'name': 'Stays seated', 'measurement_type': 'task_analysis'},
                {'name': '', 'measurement_type': 'rate'},
                'not a dict',
            ],
        }, AREAS, TAGS)
        self.assertEqual(clean['name'], 'Requesting items')
        self.assertEqual(clean['category'], 'skill_acquisition')
        self.assertEqual(clean['treatment_area'], 'Communication')       # the org's own spelling
        self.assertEqual(clean['tags'], ['Priority'])                    # unknown tag and duplicate dropped
        self.assertEqual(clean['targets'], [
            {'name': 'Asks for snack', 'measurement_type': 'discrete_trial'},
            {'name': 'Stays seated', 'measurement_type': 'discrete_trial'},  # unsupported type falls back
        ])
        self.assertNotIn('script', clean['instructions_html'])
        self.assertEqual(clean['instructions_html'], '<p>Hold up the item</p><p>xWait 5 seconds</p>')

    def test_unknown_treatment_area_is_left_empty(self):
        clean = normalize_program_draft({'name': 'X', 'treatment_area': 'Nonexistent', 'targets': []}, AREAS, TAGS)
        self.assertEqual(clean['treatment_area'], '')

    def test_target_count_is_capped(self):
        clean = normalize_program_draft({'targets': [{'name': f't{i}'} for i in range(100)]}, AREAS, TAGS)
        self.assertEqual(len(clean['targets']), MAX_TARGETS)

    def test_nothing_usable_is_an_error(self):
        with self.assertRaises(AIError):
            normalize_program_draft({'category': 'skill_acquisition'}, AREAS, TAGS)

    def test_prompt_lists_only_the_orgs_own_choices_and_forbids_copying(self):
        prompt = SYSTEM_PROMPT_TEMPLATE.format(categories='c', types='t', areas='Communication', tags='Priority')
        self.assertIn('Communication', prompt)
        self.assertIn('VB-MAPP', prompt)
        self.assertIn('Do NOT copy', prompt)

    def test_short_description_never_reaches_the_ai(self):
        with mock.patch('apps.programs.program_draft.chat_json') as call:
            with self.assertRaises(AIError) as raised:
                generate_program_draft('hi', AREAS, TAGS)
        call.assert_not_called()
        self.assertEqual(raised.exception.status, 400)

    def test_orgs_choices_are_sent_in_the_prompt(self):
        with mock.patch('apps.programs.program_draft.chat_json', return_value={'name': 'X'}) as call:
            generate_program_draft('teach a child to request items', AREAS, TAGS)
        system_prompt = call.call_args[0][0]
        self.assertIn('Social Skills', system_prompt)
        self.assertIn('Parent Training', system_prompt)


class ProgramDraftEndpointTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Draft AI Org', slug='draft-ai-org', schema_name='draft_ai_org')
        self.admin = User.objects.create_user(
            email='draftai-admin@example.com', password='x', organization=self.org, role=User.Role.ADMIN,
        )
        self.staff = User.objects.create_user(
            email='draftai-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            TreatmentArea.objects.create(name='Communication', created_by=self.admin)
            ProgramTag.objects.create(name='Priority', created_by=self.admin)

    def _request(self, user):
        return SimpleNamespace(user=user, tenant=self.org)

    def test_admin_gets_a_draft_using_the_orgs_own_lists(self):
        reply = {'name': 'Requesting', 'category': 'skill_acquisition', 'treatment_area': 'communication',
                 'tags': ['priority'], 'targets': [{'name': 'Asks for snack', 'measurement_type': 'discrete_trial'}]}
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch('apps.programs.program_draft.chat_json', return_value=reply) as call:
                draft = programs_api.draft_program_with_ai(
                    self._request(self.admin), ProgramDraftRequest(description='teach a child to request items'),
                )
        self.assertEqual((draft['treatment_area'], draft['tags']), ('Communication', ['Priority']))
        self.assertIn('Communication', call.call_args[0][0])

    def test_a_role_that_cannot_create_programs_is_refused_before_any_ai_call(self):
        RolePermission.objects.update_or_create(
            organization=self.org, role=User.Role.STAFF,
            defaults={'permissions': {'client_programs_create': False, 'org_programs_create': False}},
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch('apps.programs.program_draft.chat_json') as call:
                with self.assertRaises(HttpError) as raised:
                    programs_api.draft_program_with_ai(
                        self._request(self.staff), ProgramDraftRequest(description='teach a child to request items'),
                    )
        call.assert_not_called()
        self.assertEqual(raised.exception.status_code, 403)

    def test_ai_errors_keep_their_status(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch('apps.programs.program_draft.chat_json', side_effect=AIError('busy', status=429)):
                with self.assertRaises(HttpError) as raised:
                    programs_api.draft_program_with_ai(
                        self._request(self.admin), ProgramDraftRequest(description='teach a child to request items'),
                    )
        self.assertEqual(raised.exception.status_code, 429)
