"""get_duration_occurrences: every timed occurrence, numbered per session and target."""
from datetime import date, datetime, timedelta, timezone

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.analytics.services import get_duration_occurrences
from apps.programs.models import Program, Target
from apps.sessions.models import BehaviorEvent, SessionRun
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class DurationOccurrenceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Occ Org', slug='occ-org', schema_name='occ_org')
        self.staff = User.objects.create_user(
            email='occ-staff@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            program = Program.objects.create(name='Stay seated', category='skill_acquisition', external_client_id=1)
            self.target = Target.objects.create(program=program, name='Seated', measurement_type='duration')
            self.other_target = Target.objects.create(program=program, name='Waiting', measurement_type='duration')
            day1 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
            day2 = day1 + timedelta(days=3)
            self.session1 = SessionRun.objects.create(
                external_client_id=1, staff=self.staff, started_at=day1, session_name='Daily session',
            )
            self.session2 = SessionRun.objects.create(external_client_id=1, staff=self.staff, started_at=day2)
            self._event(self.session1, self.target, day1, 300)
            self._event(self.session1, self.target, day1 + timedelta(minutes=10), 45)
            self._event(self.session1, self.other_target, day1 + timedelta(minutes=5), 20)
            self._event(self.session1, self.target, day1 + timedelta(minutes=20), None)  # untimed: ignored
            self._event(self.session2, self.target, day2, 12)

    def _event(self, session, target, when, seconds):
        BehaviorEvent.objects.create(
            session_run=session, target_id=target.id, target_name=target.name,
            occurred_at=when, duration_seconds=seconds, frequency_count=1,
        )

    def test_numbers_occurrences_within_each_session_and_target(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            rows = get_duration_occurrences([self.target.id, self.other_target.id], date(2026, 3, 1), date(2026, 3, 10))
        summary = [(r['session_id'], r['target_name'], r['index'], r['duration_seconds']) for r in rows]
        self.assertEqual(summary, [
            (self.session1.id, 'Seated', 1, 300),
            (self.session1.id, 'Seated', 2, 45),
            (self.session1.id, 'Waiting', 1, 20),
            (self.session2.id, 'Seated', 1, 12),
        ])
        self.assertEqual(rows[0]['session_label'], 'Daily session')

    def test_respects_the_date_range(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            rows = get_duration_occurrences([self.target.id], date(2026, 3, 4), date(2026, 3, 10))
        self.assertEqual([r['duration_seconds'] for r in rows], [12])

    def test_no_targets_gives_nothing(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.assertEqual(get_duration_occurrences([], date(2026, 3, 1), date(2026, 3, 10)), [])


class ProgramSummaryForTimedTargetsTests(DurationOccurrenceTests):
    """Duration targets record timed occurrences, not trials; the summary must still count them."""

    def test_snapshot_counts_timed_occurrences_and_sessions(self):
        from apps.analytics.services import get_program_summary

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            summary = {row['target_name']: row for row in get_program_summary(
                self.target.program_id, date(2026, 3, 1), date(2026, 3, 10),
            )}
        seated = summary['Seated']
        self.assertEqual(seated['total_trials'], 4)      # 300s, 45s, untimed, 12s
        self.assertEqual(seated['total_sessions'], 2)
        self.assertEqual(str(seated['last_session_date']), '2026-03-04')
        self.assertEqual(summary['Waiting']['total_trials'], 1)

    def test_range_without_activity_is_still_zero(self):
        from apps.analytics.services import get_program_summary

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            summary = get_program_summary(self.target.program_id, date(2026, 1, 1), date(2026, 1, 5))
        self.assertTrue(all(row['total_trials'] == 0 and row['last_session_date'] is None for row in summary))

    def test_client_report_counts_timed_occurrences(self):
        from apps.analytics.services import get_client_progress_report

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            report = get_client_progress_report(1, date(2026, 3, 1), date(2026, 3, 10))
        targets = {t['target_name']: t for program in report['programs'] for t in program['targets']}
        self.assertEqual(targets['Seated']['total_trials'], 4)
        self.assertEqual(targets['Seated']['total_sessions'], 2)
        self.assertEqual(str(targets['Seated']['last_session_date']), '2026-03-04')
        self.assertEqual(targets['Waiting']['total_trials'], 1)
