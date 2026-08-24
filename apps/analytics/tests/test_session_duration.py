"""
get_trial_data_by_day's duration_seconds — sums each contributing session's
(ended_at - started_at) once per series, not once per trial, so a session
with many trials across many targets doesn't inflate the total.
"""
from datetime import date, datetime, timedelta, timezone

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.analytics.services import get_trial_data_by_day
from apps.programs.models import Program, Target
from apps.sessions.models import SessionRun, TrialEvent
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class SessionDurationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-session-duration', schema_name='test_org_session_duration',
        )
        self.staff = User.objects.create_user(
            email='staff-duration@example.com', password='x',
            first_name='Staff', last_name='User', organization=self.org, role=User.Role.STAFF,
        )

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.program = Program.objects.create(name='Manding', category='skill_acquisition', external_client_id=1)
            self.target1 = Target.objects.create(program=self.program, name='Target 1', measurement_type='discrete_trial')
            self.target2 = Target.objects.create(program=self.program, name='Target 2', measurement_type='discrete_trial')

            start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
            # 10-minute session with trials on two different targets — its
            # duration must count once per series, not once per trial.
            # started_at is auto_now_add, so it can't be set via create();
            # .update() bypasses the model save() hook that enforces that.
            self.session_timed = SessionRun.objects.create(external_client_id=1, staff=self.staff)
            SessionRun.objects.filter(pk=self.session_timed.pk).update(
                started_at=start, ended_at=start + timedelta(minutes=10),
            )
            # Still-open session (no ended_at) — contributes 0 duration.
            self.session_open = SessionRun.objects.create(external_client_id=1, staff=self.staff)
            SessionRun.objects.filter(pk=self.session_open.pk).update(started_at=start)

            TrialEvent.objects.create(
                session_run=self.session_timed, target_id=self.target1.id, target_name='Target 1',
                trial_number=1, response_score=1, prompt_level_label='Independent', recorded_at=start,
            )
            TrialEvent.objects.create(
                session_run=self.session_timed, target_id=self.target1.id, target_name='Target 1',
                trial_number=2, response_score=1, prompt_level_label='Independent', recorded_at=start,
            )
            TrialEvent.objects.create(
                session_run=self.session_timed, target_id=self.target2.id, target_name='Target 2',
                trial_number=1, response_score=1, prompt_level_label='Independent', recorded_at=start,
            )
            TrialEvent.objects.create(
                session_run=self.session_open, target_id=self.target1.id, target_name='Target 1',
                trial_number=3, response_score=1, prompt_level_label='Independent', recorded_at=start,
            )
            self.day = date(2026, 1, 1)

    def test_session_duration_counted_once_per_series_not_per_trial(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            points = get_trial_data_by_day([self.target1.id, self.target2.id], self.day, self.day)
            by_target = {p['target_id']: p for p in points}
            # target1 has 2 trials in session_timed (10 min) + 1 trial in the
            # still-open session (0s) — duration should be 600s, not 1200s.
            self.assertEqual(by_target[self.target1.id]['duration_seconds'], 600.0)
            # target2 has 1 trial in the same 10-minute session.
            self.assertEqual(by_target[self.target2.id]['duration_seconds'], 600.0)

    def test_open_session_contributes_zero_duration(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            points = get_trial_data_by_day([self.target1.id], self.day, self.day, group_by='user')
            self.assertEqual(points[0]['duration_seconds'], 600.0)
