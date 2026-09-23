"""compute_program_baseline: % correct from each target's first N sessions."""
from datetime import datetime, timedelta, timezone

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.analytics.services import compute_program_baseline
from apps.programs.models import Program, Target
from apps.sessions.models import SessionRun, TrialEvent
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class ProgramBaselineTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Baseline Org', slug='baseline-org', schema_name='baseline_org',
        )
        self.staff = User.objects.create_user(
            email='baseline-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.program = Program.objects.create(name='Manding', category='skill_acquisition', external_client_id=1)
            self.target = Target.objects.create(program=self.program, name='Ask for help', measurement_type='discrete_trial')
            self.empty_target = Target.objects.create(program=self.program, name='No data', measurement_type='discrete_trial')
            start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
            # 4 sessions on separate days; 2 trials each. Scores: s1 [1,0] s2 [1,1] s3 [0,0] s4 [1,1]
            scores_by_session = [[1, 0], [1, 1], [0, 0], [1, 1]]
            for index, scores in enumerate(scores_by_session):
                when = start + timedelta(days=index)
                session = SessionRun.objects.create(external_client_id=1, staff=self.staff, started_at=when)
                for trial_number, score in enumerate(scores, start=1):
                    TrialEvent.objects.create(
                        session_run=session, target_id=self.target.id, target_name=self.target.name,
                        trial_number=trial_number, response_score=score, prompt_level_label='',
                        recorded_at=when,
                    )

    def test_uses_only_first_n_sessions(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            (row,) = compute_program_baseline(self.program.id, sessions=3)
        self.assertEqual(row['target_name'], 'Ask for help')
        self.assertEqual(row['sessions_used'], 3)
        self.assertEqual(row['total_trials'], 6)
        self.assertEqual(row['correct_trials'], 3)
        self.assertEqual(row['percent_correct'], 50.0)
        self.assertEqual(str(row['first_date']), '2026-01-01')
        self.assertEqual(str(row['last_date']), '2026-01-03')

    def test_fewer_sessions_than_requested(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            (row,) = compute_program_baseline(self.program.id, sessions=10)
        self.assertEqual(row['sessions_used'], 4)
        self.assertEqual(row['percent_correct'], 62.5)

    def test_targets_without_data_are_omitted(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            names = [r['target_name'] for r in compute_program_baseline(self.program.id)]
        self.assertEqual(names, ['Ask for help'])
