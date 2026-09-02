"""
get_behavior_data_by_day now returns per-episode duration spread
(min/max/avg) alongside frequency and total, powering the behavior graph's
Min./Max./Avg. Observed Duration Y-axis options.
"""
from datetime import datetime, timezone

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.analytics.services import get_behavior_data_by_day
from apps.programs.models import Program, Target
from apps.sessions.models import BehaviorEvent, SessionRun
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class BehaviorDurationSpreadTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='BD Org', slug='bd-org', schema_name='bd_org',
        )
        self.staff = User.objects.create_user(
            email='bd-staff@example.com', password='x', first_name='S', last_name='S',
            organization=self.org, role=User.Role.STAFF,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.program = Program.objects.create(
                name='Tantrum', category='behavior_reduction', external_client_id=1,
            )
            self.target = Target.objects.create(
                program=self.program, name='Tantrum', measurement_type='duration',
                measurement='total_observed_duration',
            )
            self.session = SessionRun.objects.create(external_client_id=1, staff=self.staff)
            when = datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc)
            for secs in (10, 30, 50):  # min 10, max 50, avg 30, total 90
                BehaviorEvent.objects.create(
                    session_run=self.session, target_id=self.target.id, target_name='Tantrum',
                    occurred_at=when, duration_seconds=secs, frequency_count=1,
                )
            self.day = when.date()

    def test_min_max_avg_and_total_duration(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            points = get_behavior_data_by_day([self.target.id], self.day, self.day)
            self.assertEqual(len(points), 1)
            p = points[0]
            self.assertEqual(p['total_duration_seconds'], 90)
            self.assertEqual(p['min_duration_seconds'], 10.0)
            self.assertEqual(p['max_duration_seconds'], 50.0)
            self.assertEqual(p['avg_duration_seconds'], 30.0)

    def test_zero_when_no_durations_recorded(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            BehaviorEvent.objects.filter(target_id=self.target.id).update(duration_seconds=None)
            p = get_behavior_data_by_day([self.target.id], self.day, self.day)[0]
            self.assertEqual(
                (p['min_duration_seconds'], p['max_duration_seconds'], p['avg_duration_seconds']),
                (0.0, 0.0, 0.0),
            )
