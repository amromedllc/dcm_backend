from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from ninja.errors import HttpError
from pydantic import ValidationError

from apps.accounts.models import User, RolePermission
from apps.clients.models import Client
from apps.programs.models import Program, Target, TargetStatus, WorkflowTemplate
from apps.programs.measurements import rate_statistics
from apps.programs.measurements import aggregate_measurement
from apps.programs.services import _behavior_metric_value, _session_criteria_result
from apps.sessions import api
from apps.sessions.models import BehaviorEvent, SessionRun
from apps.sessions.schemas import BehaviorEventCreateRequest, SessionSubmitRequest, SessionStartRequest, GroupSessionStartRequest
from apps.sessions.services import build_program_snapshot
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class RateObservationTests(TestCase):
    def setUp(self):
        self.org = Organization(name='Synthetic rate clinic', slug='rate-test', schema_name='rate_test')
        self.other = Organization(name='Other rate clinic', slug='other-rate-test', schema_name='other_rate_test')
        Organization.objects.bulk_create([self.org, self.other])
        context = tenant_context(self.org.pk)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.admin = User.objects.create_user('rate-admin@example.test', role='admin', organization=self.org, external_admin_id=101)
        self.client = Client.objects.create(first_name='Synthetic', last_name='Rate', external_id='501', external_admin_id=101)
        self.program = Program.objects.create(name='Synthetic rate program', external_client_id=501)
        self.target = Target.objects.create(program=self.program, name='Synthetic rate', measurement_type='rate', measurement='rate_per_minute', status='acquisition')
        self.start = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
        self.session = SessionRun.objects.create(external_client_id=501, staff=self.admin, started_at=self.start,
            ended_at=self.start + timedelta(hours=1), program_snapshot={'programs': [{'targets': [{
                'id': self.target.pk, 'measurement_type': 'rate', 'measurement': 'rate_per_minute',
                'rate_observation_mode': 'target_windows',
            }]}]})
        self.request = SimpleNamespace(user=self.admin, tenant=self.org, _jwt_payload={'org_id': self.org.pk})

    def period(self, start=0, end=30, event_id='synthetic-rate-period'):
        return BehaviorEventCreateRequest(target_id=self.target.pk, target_name=self.target.name,
            occurred_at=self.start + timedelta(seconds=start), observation_ended_at=self.start + timedelta(seconds=end),
            frequency_count=0, client_event_id=event_id)

    def tally(self, second, count=1):
        return BehaviorEvent.objects.create(session_run=self.session, target_id=self.target.pk, target_name=self.target.name,
            occurred_at=self.start + timedelta(seconds=second), frequency_count=count)

    def save_period(self, **kwargs):
        return api.add_behavior(self.request, self.session.pk, self.period(**kwargs))[1]

    def test_persisted_window_summary_mastery_and_chart_agree(self):
        from apps.analytics.services import get_behavior_data_by_day
        self.save_period()
        self.tally(10)
        self.tally(20)
        self.tally(120, 100)  # Outside observation; must not inflate rate.
        self.assertEqual(_behavior_metric_value(self.target, self.session, {'objective_key': 'rate_per_minute'}), (2, 4))
        summary = api._build_trial_summary(self.session)[0]
        batch = api._trial_summaries_for_sessions([self.session])[self.session.pk][0]
        self.assertEqual((summary.total_trials, summary.measurement_value), (2, 4))
        self.assertEqual(batch.measurement_value, summary.measurement_value)
        chart = get_behavior_data_by_day([self.target.pk], self.start.date(), self.start.date())[0]
        self.assertEqual((chart['frequency'], chart['observation_seconds'], chart['measurement_value']), (2, 30, 4))

    def test_zero_observation_and_unknown_coverage_are_distinct(self):
        self.tally(10)
        self.assertEqual(_behavior_metric_value(self.target, self.session, {}), (0, None))
        self.assertIsNone(_session_criteria_result(self.target, self.session, {}, {'minimum_trials': 0, 'threshold_pct': 0, 'threshold_direction': 'max'}))
        self.save_period(start=60, end=90)
        self.assertEqual(_behavior_metric_value(self.target, self.session, {}), (0, 0))

    def test_overlap_does_not_double_denominator_and_retry_is_idempotent(self):
        first = self.save_period()
        self.assertEqual(self.save_period().pk, first.pk)
        self.save_period(start=15, end=60, event_id='overlap')
        self.tally(20, 3)
        self.assertEqual(_behavior_metric_value(self.target, self.session, {}), (3, 3))
        with self.assertRaises(HttpError) as error:
            self.save_period(end=45)
        self.assertEqual(error.exception.status_code, 409)

    def test_other_tenant_cannot_read_write_or_delete_period(self):
        event = self.save_period()
        other_admin = User.objects.create_user('other-rate-admin@example.test', role='admin', organization=self.other, external_admin_id=101)
        request = SimpleNamespace(user=other_admin, tenant=self.other, _jwt_payload={'org_id': self.other.pk})
        with tenant_context(self.other.pk):
            for call in (lambda: api.list_behaviors(request, self.session.pk),
                         lambda: api.add_behavior(request, self.session.pk, self.period()),
                         lambda: api.delete_behavior(request, self.session.pk, event.pk)):
                with self.assertRaises(HttpError) as error:
                    call()
                self.assertEqual(error.exception.status_code, 404)
            from apps.analytics.services import get_behavior_data_by_day
            self.assertEqual(get_behavior_data_by_day([self.target.pk], self.start.date(), self.start.date()), [])
        self.assertTrue(BehaviorEvent.objects.filter(pk=event.pk).exists())

    def test_missing_recording_permission_and_approved_sessions_rejected(self):
        staff = User.objects.create_user('rate-staff@example.test', role='staff', organization=self.org, external_admin_id=101)
        request = SimpleNamespace(user=staff, tenant=self.org, _jwt_payload={'org_id': self.org.pk})
        self.session.staff = staff
        self.session.save(update_fields=['staff'])
        RolePermission.objects.create(organization=self.org, role='staff', permissions={'session_start': False})
        with patch.object(api, '_accessible_external_client_ids', return_value={501}):
            with self.assertRaises(HttpError) as error:
                api.add_behavior(request, self.session.pk, self.period())
            self.assertEqual(error.exception.status_code, 403)
        self.session.status = 'approved'
        self.session.save(update_fields=['status'])
        with self.assertRaises(HttpError) as error:
            self.save_period()
        self.assertEqual(error.exception.status_code, 409)

    def test_invalid_target_and_out_of_session_period_rejected(self):
        for payload in (self.period(start=-1), self.period(end=3601), self.period().model_copy(update={'target_id': 999999})):
            with self.assertRaises(HttpError) as error:
                api.add_behavior(self.request, self.session.pk, payload)
            self.assertEqual(error.exception.status_code, 400)
        for event_id in ('', 'x' * 65):
            with self.assertRaises(ValidationError):
                self.period(event_id=event_id)

    def test_short_windows_use_real_elapsed_time_and_legacy_snapshots_keep_session_basis(self):
        self.assertEqual(aggregate_measurement('rate_per_minute', event_count=1, seconds_elapsed=10), 6)
        self.assertEqual(aggregate_measurement('rate_per_hour', event_count=1, seconds_elapsed=10), 360)
        self.assertIsNone(aggregate_measurement('rate_per_minute', event_count=1))
        self.tally(10, 6)
        self.session.program_snapshot = {}
        self.assertEqual(_behavior_metric_value(self.target, self.session, {}), (6, 0.1))

    def test_midnight_clipping_and_adjacent_periods(self):
        rows = [dict(occurred_at=self.start, observation_ended_at=self.start + timedelta(seconds=30), frequency_count=0),
                dict(occurred_at=self.start + timedelta(seconds=30), observation_ended_at=self.start + timedelta(seconds=60), frequency_count=0),
                dict(occurred_at=self.start + timedelta(seconds=30), observation_ended_at=None, frequency_count=1)]
        self.assertEqual(rate_statistics(rows), (1, 60))
        self.assertEqual(rate_statistics(rows, upper=self.start + timedelta(seconds=30)), (0, 30))
        zone = ZoneInfo('America/New_York')
        start = datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=zone)
        end = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=zone)
        self.assertEqual(rate_statistics([dict(occurred_at=start, observation_ended_at=end, frequency_count=0)]), (0, 3600))

    def test_observed_zero_can_meet_explicit_zero_occurrence_mastery_criteria_on_submit(self):
        self.save_period()
        workflow = WorkflowTemplate.objects.create(name='Synthetic zero rate', phases=[
            {'phase': 'acquisition', 'criteria': {'minimum_trials': 0, 'threshold_pct': 0,
                'threshold_direction': 'max', 'consecutive_sessions': 1}, 'on_success': 'mastered'},
            {'phase': 'mastered'},
        ])
        self.target.workflow_template = workflow
        self.target.mastery_mode = 'automatic'
        self.target.save()
        with patch('apps.notifications.service.notify_session_approved'), \
             patch('apps.notifications.service.notify_target_advanced'), \
             patch('apps.notifications.service.notify_target_mastered'):
            result = api.submit(self.request, self.session.pk, SessionSubmitRequest())
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, 'mastered')
        self.assertEqual(result['session']['status'], 'approved')
        self.assertEqual(result['advanced_targets'][0].to_status, 'mastered')

    def test_midnight_zero_days_and_weekly_denominators_include_all_target_periods(self):
        from apps.analytics.services import get_behavior_data_by_day
        midnight = self.start.replace(hour=23, minute=59, second=30)
        self.session.ended_at = midnight + timedelta(minutes=2)
        self.session.save(update_fields=['ended_at'])
        BehaviorEvent.objects.create(session_run=self.session, target_id=self.target.pk, target_name=self.target.name,
            occurred_at=midnight, observation_ended_at=midnight + timedelta(minutes=1), frequency_count=0, client_event_id='midnight')
        BehaviorEvent.objects.create(session_run=self.session, target_id=self.target.pk, target_name=self.target.name,
            occurred_at=midnight + timedelta(seconds=10), frequency_count=2)
        points = get_behavior_data_by_day([self.target.pk], midnight.date(), (midnight + timedelta(days=1)).date())
        self.assertEqual([p['observation_seconds'] for p in points], [30, 30])
        self.assertEqual([p['measurement_value'] for p in points], [4, 0])
        self.assertEqual([p['observation_windows'][0]['weekly'] for p in points], [60, 60])

    def test_web_can_opt_in_without_changing_older_client_defaults(self):
        self.assertEqual(SessionStartRequest(client_id=501).rate_observation_mode, 'session')
        self.assertEqual(GroupSessionStartRequest(clients=[]).rate_observation_mode, 'session')
        with self.assertRaises(ValidationError):
            SessionStartRequest(client_id=501, rate_observation_mode='unknown')
        TargetStatus.objects.create(key='acquisition', label='Acquisition', is_staff_visible=True)
        for mode in ('session', 'target_windows'):
            snapshot = build_program_snapshot(501, rate_observation_mode=mode)
            target = next(t for p in snapshot['programs'] for t in p['targets'] if t['id'] == self.target.pk)
            self.assertEqual(target['rate_observation_mode'], mode)

    def test_manual_occurrences_and_coverage_preserve_completed_session_edit_rules(self):
        self.session.entry_method = 'manual'
        self.session.save(update_fields=['entry_method'])
        self.save_period()
        data = BehaviorEventCreateRequest(target_id=self.target.pk, target_name=self.target.name,
            occurred_at=self.start + timedelta(seconds=10), frequency_count=2, client_event_id='manual-count')
        api.add_behavior(self.request, self.session.pk, data)
        self.assertEqual(api._build_trial_summary(self.session)[0].measurement_value, 4)
        self.session.status = 'submitted'
        self.session.save(update_fields=['status'])
        with self.assertRaises(HttpError) as error:
            self.save_period(start=30, end=60, event_id='correction')
        self.assertEqual(error.exception.status_code, 400)
        api.add_behavior(self.request, self.session.pk, self.period(start=30, end=60, event_id='correction'), reason='Synthetic coverage correction')
        self.assertEqual(api._build_trial_summary(self.session)[0].measurement_value, 2)
