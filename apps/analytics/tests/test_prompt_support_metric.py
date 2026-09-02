"""
get_trial_data_by_day now also returns prompt_level_sum / prompt_level_count
per day+series, so the graph can plot "Avg. Prompt Support" (0 == independent,
higher == more prompting) as a prompt-fading trend line.
"""
from datetime import date, datetime, timezone

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.analytics.services import get_trial_data_by_day
from apps.programs.models import Program, PromptingTemplate, Target
from apps.sessions.models import SessionRun, TrialEvent
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


# most-intrusive first — ranks become FP=3, PP=2, Model=1, Independent=0
_LEVELS = [
    {'label': 'Full Physical', 'score': 0, 'is_success': False},
    {'label': 'Partial Physical', 'score': 1, 'is_success': False},
    {'label': 'Model', 'score': 2, 'is_success': False},
    {'label': 'Independent', 'score': 3, 'is_success': True},
]


class PromptSupportMetricTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='PS Org', slug='ps-org', schema_name='ps_org',
        )
        self.staff = User.objects.create_user(
            email='ps-staff@example.com', password='x', first_name='S', last_name='S',
            organization=self.org, role=User.Role.STAFF,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.tmpl = PromptingTemplate.objects.create(name='4-level', levels=_LEVELS)
            self.program = Program.objects.create(name='Mand', category='skill_acquisition', external_client_id=1)
            self.target = Target.objects.create(
                program=self.program, name='T1', measurement_type='discrete_trial',
                prompting_template=self.tmpl,
            )
            self.session = SessionRun.objects.create(external_client_id=1, staff=self.staff)
            when = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
            for i, label in enumerate(['Independent', 'Model', 'Full Physical']):  # ranks 0 + 1 + 3
                TrialEvent.objects.create(
                    session_run=self.session, target_id=self.target.id, target_name='T1',
                    trial_number=i + 1, response_score=1, prompt_level_label=label, recorded_at=when,
                )
            self.day = date(2026, 3, 1)

    def test_prompt_rank_sum_and_count(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            p = get_trial_data_by_day([self.target.id], self.day, self.day)[0]
            self.assertEqual(p['prompt_level_count'], 3)
            self.assertEqual(p['prompt_level_sum'], 4.0)  # 0 + 1 + 3

    def test_unmapped_labels_are_ignored(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            TrialEvent.objects.create(
                session_run=self.session, target_id=self.target.id, target_name='T1',
                trial_number=99, response_score=1, prompt_level_label='Gesture (not in template)',
                recorded_at=datetime(2026, 3, 1, 10, 5, tzinfo=timezone.utc),
            )
            p = get_trial_data_by_day([self.target.id], self.day, self.day)[0]
            self.assertEqual(p['prompt_level_count'], 3)  # unchanged
            self.assertEqual(p['total_trials'], 4)

    def test_no_prompting_template_yields_zero(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            bare = Target.objects.create(
                program=self.program, name='T2', measurement_type='discrete_trial',
            )
            TrialEvent.objects.create(
                session_run=self.session, target_id=bare.id, target_name='T2',
                trial_number=1, response_score=1, prompt_level_label='Independent',
                recorded_at=datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc),
            )
            p = get_trial_data_by_day([bare.id], self.day, self.day)[0]
            self.assertEqual((p['prompt_level_sum'], p['prompt_level_count']), (0.0, 0))
