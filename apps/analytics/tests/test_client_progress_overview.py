"""
get_client_progress_overview — raw mastery events (target/program/treatment
area/tags/status + timestamp) and per-program rollup (status counts, avg
trials/sessions to mastery) across all of a client's programs at once.
Powers the top-level Progress screen.
"""
from datetime import date, datetime, timezone

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.clients.models import Client
from apps.analytics.services import get_client_progress_overview
from apps.programs.models import Program, Target, TargetStatusChange
from apps.sessions.models import SessionRun, TrialEvent
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class ClientProgressOverviewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-progress-overview', schema_name='test_org_progress_overview',
        )

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.client_obj = Client.objects.create(
                first_name='Jamie', last_name='Doe', external_id='501',
            )

            self.program_a = Program.objects.create(
                name='Manding', category='skill_acquisition', treatment_area='Language and Communication',
                external_client_id=self.client_obj.id, tags=['ABA', 'Manding'],
            )
            self.program_b = Program.objects.create(
                name='Aggression', category='behavior_reduction', treatment_area='Behavior Intervention',
                external_client_id=self.client_obj.id,
            )

            # program_a: one mastered target, one still in acquisition
            self.mastered_target = Target.objects.create(
                program=self.program_a, name='Requests juice', measurement_type='discrete_trial', status='mastered',
            )
            self.active_target = Target.objects.create(
                program=self.program_a, name='Requests snack', measurement_type='discrete_trial', status='acquisition',
            )
            # program_b: one waiting target
            self.other_target = Target.objects.create(
                program=self.program_b, name='Hitting', measurement_type='frequency', status='waiting',
            )

            session = SessionRun.objects.create(external_client_id=self.client_obj.id)

            self.mastered_at = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
            before = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
            after = datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc)

            # 3 trials before/at mastery (should count), 1 after (should not)
            for i, when in enumerate([before, before, self.mastered_at, after]):
                TrialEvent.objects.create(
                    session_run=session, target_id=self.mastered_target.id, target_name='Requests juice',
                    trial_number=i + 1, response_score=1, prompt_level_label='Independent', recorded_at=when,
                )

            TargetStatusChange.objects.create(
                target=self.mastered_target, from_status='acquisition', to_status='mastered',
                trigger='manual',
            )
            # Backdate created_at to the intended mastery timestamp — auto_now_add
            # stamps 'now' on create, so it must be overwritten with a queryset update.
            TargetStatusChange.objects.filter(target=self.mastered_target).update(created_at=self.mastered_at)

    def test_mastery_events_carries_target_program_and_timestamp(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            result = get_client_progress_overview(self.client_obj.id)
            self.assertEqual(len(result['mastery_events']), 1)
            event = result['mastery_events'][0]
            self.assertEqual(event['target_id'], self.mastered_target.id)
            self.assertEqual(event['target_name'], 'Requests juice')
            self.assertEqual(event['program_id'], self.program_a.id)
            self.assertEqual(event['program_name'], 'Manding')
            self.assertEqual(event['treatment_area'], 'Language and Communication')
            self.assertEqual(event['program_status'], 'active')
            self.assertEqual(event['program_tags'], ['ABA', 'Manding'])
            self.assertEqual(event['mastered_at'], self.mastered_at)

    def test_program_rollup_status_counts_and_mastery_stats(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            result = get_client_progress_overview(self.client_obj.id)
            by_id = {p['program_id']: p for p in result['programs']}

            program_a = by_id[self.program_a.id]
            self.assertEqual(program_a['status_counts'], {'mastered': 1, 'acquisition': 1})
            self.assertEqual(program_a['avg_trials_to_mastery'], 3.0)
            self.assertEqual(program_a['avg_sessions_to_mastery'], 1.0)
            self.assertEqual(program_a['treatment_area'], 'Language and Communication')
            self.assertEqual(program_a['status'], 'active')
            self.assertEqual(program_a['tags'], ['ABA', 'Manding'])

            program_b = by_id[self.program_b.id]
            self.assertEqual(program_b['status_counts'], {'waiting': 1})
            self.assertIsNone(program_b['avg_trials_to_mastery'])
            self.assertIsNone(program_b['avg_sessions_to_mastery'])

    def test_accepts_external_client_id_like_progress_report(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            result = get_client_progress_overview(int(self.client_obj.external_id))
            self.assertEqual(len(result['programs']), 2)
