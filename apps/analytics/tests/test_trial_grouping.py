"""
get_trial_data_by_day's group_by param — 'target' (default), 'prompt_level',
and 'user' each collapse TrialEvent rows into a different series dimension.
"""
from datetime import date, datetime, timezone

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.analytics.services import get_trial_data_by_day
from apps.programs.models import Program, Target
from apps.sessions.models import SessionRun, TrialEvent
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class TrialGroupingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-trial-grouping', schema_name='test_org_trial_grouping',
        )
        self.rbt_a = User.objects.create_user(
            email='rbt-a@example.com', password='x', first_name='Ann', last_name='A',
            organization=self.org, role=User.Role.STAFF,
        )
        self.rbt_b = User.objects.create_user(
            email='rbt-b@example.com', password='x', first_name='Bo', last_name='B',
            organization=self.org, role=User.Role.STAFF,
        )

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.program = Program.objects.create(name='Manding', category='skill_acquisition', external_client_id=1)
            self.target1 = Target.objects.create(program=self.program, name='Target 1', measurement_type='discrete_trial')
            self.target2 = Target.objects.create(program=self.program, name='Target 2', measurement_type='discrete_trial')

            self.session_a = SessionRun.objects.create(external_client_id=1, staff=self.rbt_a)
            self.session_b = SessionRun.objects.create(external_client_id=1, staff=self.rbt_b)

            day = date(2026, 1, 1)
            when = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

            # session_a: target1 @ Full Physical (correct), target2 @ Independent (correct)
            TrialEvent.objects.create(
                session_run=self.session_a, target_id=self.target1.id, target_name='Target 1',
                trial_number=1, response_score=1, prompt_level_label='Full Physical', recorded_at=when,
            )
            TrialEvent.objects.create(
                session_run=self.session_a, target_id=self.target2.id, target_name='Target 2',
                trial_number=1, response_score=1, prompt_level_label='Independent', recorded_at=when,
            )
            # session_b: target1 @ Independent (correct)
            TrialEvent.objects.create(
                session_run=self.session_b, target_id=self.target1.id, target_name='Target 1',
                trial_number=1, response_score=1, prompt_level_label='Independent', recorded_at=when,
            )
            self.day = day

    def test_group_by_target_is_default_and_unchanged(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            points = get_trial_data_by_day([self.target1.id, self.target2.id], self.day, self.day)
            by_target = {p['target_id']: p for p in points}
            self.assertEqual(set(by_target.keys()), {self.target1.id, self.target2.id})
            self.assertEqual(by_target[self.target1.id]['total_trials'], 2)
            self.assertEqual(by_target[self.target2.id]['total_trials'], 1)

    def test_group_by_prompt_level_collapses_across_targets(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            points = get_trial_data_by_day([self.target1.id, self.target2.id], self.day, self.day, group_by='prompt_level')
            by_label = {p['target_id']: p for p in points}
            self.assertEqual(set(by_label.keys()), {'Full Physical', 'Independent'})
            # Independent combines target1's session_b trial + target2's session_a trial
            self.assertEqual(by_label['Independent']['total_trials'], 2)
            self.assertEqual(by_label['Full Physical']['total_trials'], 1)

    def test_group_by_user_collapses_across_targets(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            points = get_trial_data_by_day([self.target1.id, self.target2.id], self.day, self.day, group_by='user')
            by_staff = {p['target_name']: p for p in points}
            self.assertEqual(set(by_staff.keys()), {'Ann A', 'Bo B'})
            self.assertEqual(by_staff['Ann A']['total_trials'], 2)
            self.assertEqual(by_staff['Bo B']['total_trials'], 1)

    def test_empty_target_ids_returns_empty(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.assertEqual(get_trial_data_by_day([], self.day, self.day, group_by='user'), [])
