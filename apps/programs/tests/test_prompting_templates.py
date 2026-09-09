from django.db import connection
from django.test import Client as DjangoClient, TestCase
from django_tenants.utils import schema_context

from apps.accounts.auth import create_access_token
from apps.accounts.models import User
from apps.clients.models import Client
from apps.programs.models import Program, PromptingTemplate, Target, TargetPromptLevelChange, TargetStatus
from apps.programs.services import _fade_if_criteria_met
from apps.sessions.models import SessionRun, TrialEvent
from apps.sessions.services import build_program_snapshot
from apps.tenants.models import Domain, Organization
from shared.tenancy import tenant_context


class PromptingTemplateBehaviorTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Prompt Org', slug='prompt-org', schema_name='prompt_org',
        )
        Domain.objects.create(domain='localhost', tenant=self.org, is_primary=True)
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='x',
            first_name='Admin',
            last_name='User',
            organization=self.org,
            role=User.Role.ADMIN,
            external_admin_id=501,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.client_row = Client.objects.create(
                first_name='Test',
                last_name='Client',
                external_id='7001',
                external_admin_id=501,
                organization=self.org,
            )

    def _api(self) -> DjangoClient:
        token = create_access_token(self.admin, self.org.pk)
        client = DjangoClient(HTTP_AUTHORIZATION=f'Bearer {token}', HTTP_HOST='localhost')
        self.addCleanup(connection.set_schema_to_public)
        return client

    def test_prompting_template_weights_are_saved_as_scores(self):
        response = self._api().post(
            '/api/v1/programs/templates/prompting',
            data={
                'name': 'Weighted Prompting',
                'outcome_measurement': 'weighted',
                'levels': [
                    {'label': 'Full Physical', 'color': '#ef4444', 'abbreviation': 'FP', 'weight': 0},
                    {'label': 'Gesture', 'color': '#f97316', 'abbreviation': 'G', 'weight': 5},
                    {'label': 'Independent', 'color': '#22c55e', 'abbreviation': 'I', 'weight': 10, 'is_success': True},
                ],
                'is_org_default': True,
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        levels = response.json()['levels']
        self.assertEqual([level['score'] for level in levels], [0, 5, 10])
        self.assertEqual(levels[-1]['weight'], 10)
        self.assertEqual(response.json()['outcome_measurement'], 'weighted')

    def test_prompting_template_advanced_settings_are_saved(self):
        response = self._api().post(
            '/api/v1/programs/templates/prompting',
            data={
                'name': 'Most to Least',
                'fading_hint_mode': 'across_sessions',
                'fading_hint_settings': {
                    'initial_prompt_level': 'Full Physical',
                    'decrease_match_pct': 100,
                    'decrease_across': 2,
                    'increase_match_pct': 0,
                    'increase_across': 2,
                    'use_simple_responses': True,
                },
                'levels': [
                    {'label': 'Independent', 'score': 1, 'weight': 1, 'color': '#22c55e', 'abbreviation': 'I', 'is_success': True},
                    {'label': 'Gesture', 'score': 0, 'weight': 0, 'color': '#eab308', 'abbreviation': 'G'},
                    {'label': 'No Response', 'score': 0, 'weight': 0, 'color': '#ef4444', 'abbreviation': 'NR', 'exclude_from_fading': True},
                    {'label': 'Full Physical', 'score': 0, 'weight': 0, 'color': '#3b82f6', 'abbreviation': 'FP'},
                ],
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['fading_hint_mode'], 'across_sessions')
        self.assertTrue(body['fading_hint_settings']['use_simple_responses'])
        self.assertTrue(body['levels'][2]['exclude_from_fading'])

    def test_prompting_template_rating_scale_is_saved_without_success_toggle(self):
        response = self._api().post(
            '/api/v1/programs/templates/prompting',
            data={
                'name': 'Rating Scale',
                'outcome_measurement': 'rating_scale',
                'fading_hint_mode': 'across_trials',
                'levels': [
                    {'label': 'Low', 'score': 1, 'weight': 1, 'color': '#ef4444', 'abbreviation': 'L'},
                    {'label': 'High', 'score': 2, 'weight': 2, 'color': '#22c55e', 'abbreviation': 'H'},
                ],
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['outcome_measurement'], 'rating_scale')
        self.assertEqual(body['fading_hint_mode'], 'none')
        self.assertEqual([level['score'] for level in body['levels']], [1, 2])
        self.assertFalse(any(level.get('is_success') for level in body['levels']))

    def test_new_program_uses_default_prompting_template(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = PromptingTemplate.objects.create(
                name='Default Prompting',
                levels=[{'label': 'Independent', 'score': 1, 'weight': 1, 'color': '#22c55e', 'abbreviation': 'I', 'is_success': True}],
                is_org_default=True,
                created_by=self.admin,
            )

        response = self._api().post(
            '/api/v1/programs',
            data={'client_id': int(self.client_row.external_id), 'name': 'New Program'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['prompting_template_id'], template.id)

    def test_target_uses_prompting_template_initial_level(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = PromptingTemplate.objects.create(
                name='Most to Least',
                fading_hint_mode=PromptingTemplate.FadingHintMode.ACROSS_SESSIONS,
                fading_hint_settings={'initial_prompt_level': 'Full Physical'},
                levels=[
                    {'label': 'Independent', 'score': 1, 'weight': 1, 'color': '#22c55e', 'abbreviation': 'I', 'is_success': True},
                    {'label': 'Gesture', 'score': 0, 'weight': 0, 'color': '#eab308', 'abbreviation': 'G'},
                    {'label': 'Full Physical', 'score': 0, 'weight': 0, 'color': '#3b82f6', 'abbreviation': 'FP'},
                ],
                created_by=self.admin,
            )
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Program',
                prompting_template=template,
                created_by=self.admin,
            )

        response = self._api().post(
            f'/api/v1/programs/{program.id}/targets',
            data={'name': 'Trial Target', 'measurement_type': Target.MeasurementType.DISCRETE_TRIAL},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['current_prompt_level_index'], 2)

    def test_target_initial_level_can_use_last_probe_trial(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = PromptingTemplate.objects.create(
                name='Probe Last Trial',
                fading_hint_mode=PromptingTemplate.FadingHintMode.ACROSS_SESSIONS,
                fading_hint_settings={'initial_prompt_strategy': 'last_probe_trial'},
                levels=[
                    {'label': 'Independent', 'score': 1, 'weight': 1, 'color': '#22c55e', 'abbreviation': 'I', 'is_success': True},
                    {'label': 'Gesture', 'score': 0, 'weight': 0, 'color': '#eab308', 'abbreviation': 'G'},
                    {'label': 'Full Physical', 'score': 0, 'weight': 0, 'color': '#3b82f6', 'abbreviation': 'FP'},
                ],
                created_by=self.admin,
            )
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Probe Program',
                created_by=self.admin,
            )
            target = Target.objects.create(
                program=program,
                name='Probe Target',
                status='acquisition',
                created_by=self.admin,
            )
            session = SessionRun.objects.create(
                external_client_id=int(self.client_row.external_id),
                staff=self.admin,
                status=SessionRun.Status.APPROVED,
                submitted_at='2026-09-08T00:00:00Z',
                program_snapshot={'programs': [{'targets': [{'id': target.id, 'status': 'probe'}]}]},
            )
            TrialEvent.objects.create(
                session_run=session,
                target_id=target.id,
                target_name=target.name,
                trial_number=1,
                response_score=0,
                prompt_level_label='Full Physical',
                recorded_at='2026-09-08T00:00:00Z',
            )
            TrialEvent.objects.create(
                session_run=session,
                target_id=target.id,
                target_name=target.name,
                trial_number=2,
                response_score=0,
                prompt_level_label='Gesture',
                recorded_at='2026-09-08T00:01:00Z',
            )

        response = self._api().patch(
            f'/api/v1/targets/{target.id}',
            data={'prompting_template_id': template.id},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['current_prompt_level_index'], 1)

    def test_target_initial_level_can_use_mass_trial_probe_threshold(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = PromptingTemplate.objects.create(
                name='Mass Probe',
                fading_hint_mode=PromptingTemplate.FadingHintMode.ACROSS_SESSIONS,
                fading_hint_settings={'initial_prompt_strategy': 'mass_trial_probe', 'probe_threshold': 2},
                levels=[
                    {'label': 'Independent', 'score': 1, 'weight': 1, 'color': '#22c55e', 'abbreviation': 'I', 'is_success': True},
                    {'label': 'Gesture', 'score': 0, 'weight': 0, 'color': '#eab308', 'abbreviation': 'G'},
                    {'label': 'Full Physical', 'score': 0, 'weight': 0, 'color': '#3b82f6', 'abbreviation': 'FP'},
                ],
                created_by=self.admin,
            )
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Mass Program',
                created_by=self.admin,
            )
            target = Target.objects.create(
                program=program,
                name='Mass Target',
                status='acquisition',
                created_by=self.admin,
            )
            session = SessionRun.objects.create(
                external_client_id=int(self.client_row.external_id),
                staff=self.admin,
                status=SessionRun.Status.APPROVED,
                submitted_at='2026-09-08T00:00:00Z',
                program_snapshot={'programs': [{'targets': [{'id': target.id, 'status': 'probe'}]}]},
            )
            for trial_number, label in enumerate(['Independent', 'Gesture', 'Gesture', 'Full Physical'], start=1):
                TrialEvent.objects.create(
                    session_run=session,
                    target_id=target.id,
                    target_name=target.name,
                    trial_number=trial_number,
                    response_score=1 if label == 'Independent' else 0,
                    prompt_level_label=label,
                    recorded_at='2026-09-08T00:00:00Z',
                )

        response = self._api().patch(
            f'/api/v1/targets/{target.id}',
            data={'prompting_template_id': template.id},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['current_prompt_level_index'], 1)

    def test_program_prompting_template_assignment_sets_target_initial_level(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = PromptingTemplate.objects.create(
                name='Program Errorless',
                fading_hint_mode=PromptingTemplate.FadingHintMode.ACROSS_SESSIONS,
                fading_hint_settings={'initial_prompt_strategy': 'errorless'},
                levels=[
                    {'label': 'Independent', 'score': 1, 'weight': 1, 'color': '#22c55e', 'abbreviation': 'I', 'is_success': True},
                    {'label': 'Gesture', 'score': 0, 'weight': 0, 'color': '#eab308', 'abbreviation': 'G'},
                    {'label': 'Full Physical', 'score': 0, 'weight': 0, 'color': '#3b82f6', 'abbreviation': 'FP'},
                ],
                created_by=self.admin,
            )
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Program Assignment',
                created_by=self.admin,
            )
            target = Target.objects.create(
                program=program,
                name='Existing Target',
                current_prompt_level_index=0,
                created_by=self.admin,
            )

        response = self._api().patch(
            f'/api/v1/programs/{program.id}',
            data={'prompting_template_id': template.id},
            content_type='application/json',
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            target.refresh_from_db()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(target.prompting_template_id, template.id)
            self.assertEqual(target.current_prompt_level_index, 2)

    def test_data_target_requires_prompting_template(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='No Prompt Program',
                created_by=self.admin,
            )

        response = self._api().post(
            f'/api/v1/programs/{program.id}/targets',
            data={'name': 'Trial Target', 'measurement_type': Target.MeasurementType.DISCRETE_TRIAL},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('Prompt level template is required', response.json()['detail'])

    def test_locked_prompting_template_blocks_edit_and_delete_but_can_unlock(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = PromptingTemplate.objects.create(
                name='Locked Template',
                is_locked=True,
                levels=[
                    {'label': 'Independent', 'score': 1, 'weight': 1, 'color': '#22c55e', 'abbreviation': 'I', 'is_success': True},
                ],
                created_by=self.admin,
            )

        edit_response = self._api().patch(
            f'/api/v1/programs/templates/prompting/{template.id}',
            data={'name': 'Changed'},
            content_type='application/json',
        )
        delete_response = self._api().delete(f'/api/v1/programs/templates/prompting/{template.id}')
        unlock_response = self._api().patch(
            f'/api/v1/programs/templates/prompting/{template.id}',
            data={'is_locked': False},
            content_type='application/json',
        )

        self.assertEqual(edit_response.status_code, 423)
        self.assertEqual(delete_response.status_code, 423)
        self.assertEqual(unlock_response.status_code, 200)
        self.assertFalse(unlock_response.json()['is_locked'])

    def test_program_hidden_prompt_levels_are_removed_from_session_snapshot(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = PromptingTemplate.objects.create(
                name='Prompting',
                levels=[
                    {'label': 'Full Physical', 'score': 0, 'weight': 0, 'color': '#ef4444', 'abbreviation': 'FP'},
                    {'label': 'Prompted', 'score': 5, 'weight': 5, 'color': '#f97316', 'abbreviation': 'P'},
                    {'label': 'Independent', 'score': 10, 'weight': 10, 'color': '#22c55e', 'abbreviation': 'I', 'is_success': True},
                ],
                created_by=self.admin,
            )
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Customized Program',
                prompting_template=template,
                hidden_prompt_level_labels=['Prompted'],
                created_by=self.admin,
            )
            Target.objects.create(
                program=program,
                name='Visible Target',
                prompting_template=template,
                status='acquisition',
                created_by=self.admin,
            )
            TargetStatus.objects.create(
                key='acquisition',
                label='Acquisition',
                is_staff_visible=True,
                created_by=self.admin,
            )

            snapshot = build_program_snapshot(int(self.client_row.external_id))

        levels = snapshot['programs'][0]['targets'][0]['prompting_template']['levels']
        self.assertEqual([level['label'] for level in levels], ['Full Physical', 'Independent'])

    def test_across_session_fading_skips_excluded_levels_and_records_template_indexes(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = PromptingTemplate.objects.create(
                name='Fading',
                fading_hint_mode=PromptingTemplate.FadingHintMode.ACROSS_SESSIONS,
                fading_hint_settings={'decrease_match_pct': 100, 'decrease_across': 1},
                levels=[
                    {'label': 'Independent', 'score': 1, 'weight': 1, 'color': '#22c55e', 'abbreviation': 'I', 'is_success': True},
                    {'label': 'Gesture', 'score': 1, 'weight': 1, 'color': '#eab308', 'abbreviation': 'G'},
                    {'label': 'No Response', 'score': 0, 'weight': 0, 'color': '#ef4444', 'abbreviation': 'NR', 'exclude_from_fading': True},
                    {'label': 'Full Physical', 'score': 1, 'weight': 1, 'color': '#3b82f6', 'abbreviation': 'FP'},
                ],
                created_by=self.admin,
            )
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Fading Program',
                prompting_template=template,
                created_by=self.admin,
            )
            target = Target.objects.create(
                program=program,
                name='Fade Target',
                prompting_template=template,
                fading_mode=Target.FadingMode.AUTOMATIC,
                current_prompt_level_index=3,
                status='acquisition',
                created_by=self.admin,
            )
            session = SessionRun.objects.create(
                external_client_id=int(self.client_row.external_id),
                staff=self.admin,
                status=SessionRun.Status.APPROVED,
                submitted_at='2026-09-08T00:00:00Z',
            )
            TrialEvent.objects.create(
                session_run=session,
                target_id=target.id,
                target_name=target.name,
                trial_number=1,
                response_score=1,
                prompt_level_label='Full Physical',
                recorded_at='2026-09-08T00:00:00Z',
            )

            faded = _fade_if_criteria_met(target, session.id)
            target.refresh_from_db()
            change = TargetPromptLevelChange.objects.get(target=target)

        self.assertTrue(faded)
        self.assertEqual(target.current_prompt_level_index, 1)
        self.assertEqual(change.from_level_index, 3)
        self.assertEqual(change.to_level_index, 1)

    def test_across_trial_fading_records_prompt_level_history(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = PromptingTemplate.objects.create(
                name='Across Trial Fading',
                fading_hint_mode=PromptingTemplate.FadingHintMode.ACROSS_TRIALS,
                fading_hint_settings={'decrease_match_pct': 100, 'decrease_across': 3},
                levels=[
                    {'label': 'Independent', 'score': 1, 'weight': 1, 'color': '#22c55e', 'is_success': True},
                    {'label': 'Partial Physical', 'score': 1, 'weight': 1, 'color': '#3b82f6'},
                    {'label': 'Full Physical', 'score': 1, 'weight': 1, 'color': '#ef4444'},
                ],
                created_by=self.admin,
            )
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Across Trial Program',
                prompting_template=template,
                created_by=self.admin,
            )
            target = Target.objects.create(
                program=program,
                name='Across Trial Target',
                prompting_template=template,
                fading_mode=Target.FadingMode.AUTOMATIC,
                current_prompt_level_index=2,
                status='acquisition',
                created_by=self.admin,
            )
            session = SessionRun.objects.create(
                external_client_id=int(self.client_row.external_id),
                staff=self.admin,
                status=SessionRun.Status.SUBMITTED,
                submitted_at='2026-09-08T00:00:00Z',
            )
            for trial_number in range(1, 4):
                TrialEvent.objects.create(
                    session_run=session,
                    target_id=target.id,
                    target_name=target.name,
                    trial_number=trial_number,
                    response_score=1,
                    prompt_level_label='Full Physical',
                    recorded_at='2026-09-08T00:00:00Z',
                )

            faded = _fade_if_criteria_met(target, session.id)
            target.refresh_from_db()
            change = TargetPromptLevelChange.objects.get(target=target)

        self.assertTrue(faded)
        self.assertEqual(target.current_prompt_level_index, 1)
        self.assertEqual(change.from_level_label, 'Full Physical')
        self.assertEqual(change.to_level_label, 'Partial Physical')

    def test_program_prompting_template_drives_target_fading_history(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = PromptingTemplate.objects.create(
                name='Program Level Fading',
                fading_hint_mode=PromptingTemplate.FadingHintMode.ACROSS_TRIALS,
                fading_hint_settings={'decrease_match_pct': 100, 'decrease_across': 2},
                levels=[
                    {'label': 'Independent', 'score': 1, 'weight': 1, 'color': '#22c55e', 'is_success': True},
                    {'label': 'Partial Physical', 'score': 1, 'weight': 1, 'color': '#3b82f6'},
                    {'label': 'Full Physical', 'score': 1, 'weight': 1, 'color': '#ef4444'},
                ],
                created_by=self.admin,
            )
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Program Template Fading Program',
                prompting_template=template,
                created_by=self.admin,
            )
            target = Target.objects.create(
                program=program,
                name='Program Template Target',
                prompting_template=None,
                fading_mode=Target.FadingMode.AUTOMATIC,
                current_prompt_level_index=2,
                status='acquisition',
                created_by=self.admin,
            )
            session = SessionRun.objects.create(
                external_client_id=int(self.client_row.external_id),
                staff=self.admin,
                status=SessionRun.Status.SUBMITTED,
                submitted_at='2026-09-08T00:00:00Z',
            )
            for trial_number in range(1, 3):
                TrialEvent.objects.create(
                    session_run=session,
                    target_id=target.id,
                    target_name=target.name,
                    trial_number=trial_number,
                    response_score=1,
                    prompt_level_label='Full Physical',
                    recorded_at='2026-09-08T00:00:00Z',
                )

            faded = _fade_if_criteria_met(target, session.id)
            target.refresh_from_db()
            change = TargetPromptLevelChange.objects.get(target=target)

        self.assertTrue(faded)
        self.assertEqual(target.current_prompt_level_index, 1)
        self.assertEqual(change.from_level_label, 'Full Physical')
        self.assertEqual(change.to_level_label, 'Partial Physical')
