from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.clients.models import Client
from apps.programs.models import Program, Target, TargetStatusChange, WorkflowTemplate
from apps.programs.services import _advance_if_criteria_met
from apps.sessions.models import SessionRun, TrialEvent
from apps.tenants.models import Domain, Organization
from shared.tenancy import tenant_context


class MasteryWorkflowBehaviorTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Workflow Org', slug='workflow-org', schema_name='workflow_org',
        )
        Domain.objects.create(domain='localhost', tenant=self.org, is_primary=True)
        self.admin = User.objects.create_user(
            email='workflow-admin@example.com',
            password='x',
            first_name='Workflow',
            last_name='Admin',
            organization=self.org,
            role=User.Role.ADMIN,
            external_admin_id=601,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.client_row = Client.objects.create(
                first_name='Workflow',
                last_name='Client',
                external_id='8001',
                external_admin_id=601,
                organization=self.org,
            )

    def _workflow(self):
        return WorkflowTemplate.objects.create(
            name='80 Percent Across Two Sessions',
            phases=[
                {
                    'phase': 'acquisition',
                    'criteria': {
                        'threshold_pct': 80,
                        'consecutive_sessions': 2,
                        'minimum_trials': 3,
                    },
                    'on_success': 'mastered',
                },
                {'phase': 'mastered', 'on_success': 'maintenance'},
            ],
            created_by=self.admin,
        )

    def _session_with_trials(self, target, trial_count: int, correct_count: int, day: int):
        session = SessionRun.objects.create(
            external_client_id=int(self.client_row.external_id),
            staff=self.admin,
            status=SessionRun.Status.SUBMITTED,
            submitted_at=f'2026-09-{day:02d}T00:00:00Z',
        )
        for trial_number in range(1, trial_count + 1):
            TrialEvent.objects.create(
                session_run=session,
                target_id=target.id,
                target_name=target.name,
                trial_number=trial_number,
                response_score=1 if trial_number <= correct_count else 0,
                prompt_level_label='Independent' if trial_number <= correct_count else 'Incorrect',
                recorded_at=f'2026-09-{day:02d}T00:00:00Z',
            )
        return session

    def test_program_workflow_template_advances_target_status(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            workflow = self._workflow()
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Program Workflow',
                workflow_template=workflow,
                created_by=self.admin,
            )
            target = Target.objects.create(
                program=program,
                name='Program Workflow Target',
                status='acquisition',
                mastery_mode=Target.MasteryMode.AUTOMATIC,
                created_by=self.admin,
            )
            self._session_with_trials(target, 5, 4, 8)
            session = self._session_with_trials(target, 5, 4, 9)

            advanced = _advance_if_criteria_met(target, session.id)
            target.refresh_from_db()
            change = TargetStatusChange.objects.get(target=target)

        self.assertTrue(advanced)
        self.assertEqual(target.status, 'mastered')
        self.assertEqual(change.from_status, 'acquisition')
        self.assertEqual(change.to_status, 'mastered')

    def test_below_minimum_trial_session_is_excluded_from_mastery_window(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            workflow = self._workflow()
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Minimum Trial Workflow',
                workflow_template=workflow,
                created_by=self.admin,
            )
            target = Target.objects.create(
                program=program,
                name='Minimum Trial Target',
                status='acquisition',
                mastery_mode=Target.MasteryMode.AUTOMATIC,
                created_by=self.admin,
            )
            self._session_with_trials(target, 5, 4, 7)
            self._session_with_trials(target, 2, 2, 8)
            session = self._session_with_trials(target, 5, 4, 9)

            advanced = _advance_if_criteria_met(target, session.id)
            target.refresh_from_db()

        self.assertTrue(advanced)
        self.assertEqual(target.status, 'mastered')

    def test_workflow_can_advance_through_multiple_acquisition_phases(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            workflow = WorkflowTemplate.objects.create(
                name='Two Acquisition Phases',
                phases=[
                    {
                        'phase': 'acquisition',
                        'label': 'Acquisition 1',
                        'criteria': {
                            'threshold_pct': 80,
                            'consecutive_sessions': 1,
                            'minimum_trials': 3,
                        },
                        'on_success': 'acquisition_2',
                    },
                    {
                        'phase': 'acquisition_2',
                        'label': 'Acquisition 2',
                        'criteria': {
                            'threshold_pct': 90,
                            'consecutive_sessions': 1,
                            'minimum_trials': 3,
                        },
                        'on_success': 'mastered',
                    },
                    {'phase': 'mastered', 'on_success': 'maintenance'},
                ],
                created_by=self.admin,
            )
            program = Program.objects.create(
                external_client_id=int(self.client_row.external_id),
                name='Multi Phase Workflow',
                workflow_template=workflow,
                created_by=self.admin,
            )
            target = Target.objects.create(
                program=program,
                name='Multi Phase Target',
                status='acquisition',
                mastery_mode=Target.MasteryMode.AUTOMATIC,
                created_by=self.admin,
            )

            session_one = self._session_with_trials(target, 5, 4, 10)
            self.assertTrue(_advance_if_criteria_met(target, session_one.id))
            target.refresh_from_db()
            self.assertEqual(target.status, 'acquisition_2')

            session_two = self._session_with_trials(target, 10, 9, 11)
            self.assertTrue(_advance_if_criteria_met(target, session_two.id))
            target.refresh_from_db()

        self.assertEqual(target.status, 'mastered')
