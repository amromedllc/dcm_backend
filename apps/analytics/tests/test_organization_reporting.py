from datetime import date, datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from ninja.errors import HttpError
from apps.accounts.models import User, RolePermission
from apps.clients.models import Client, ClientStatusObservation
from apps.clients.status_history import record_client_statuses
from apps.programs.models import Program, Target, TargetStatusChange
from apps.sessions.models import SessionRun
from apps.notes.models import LessonNote
from apps.tenants.models import Organization
from apps.analytics.api import organization_analytics
from shared.tenancy import tenant_context

NOW = datetime(2026, 11, 4, 12, tzinfo=dt_timezone.utc)


class OrganizationReportingTests(TestCase):
    def setUp(self):
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.org = Organization(name='Synthetic Clinic', schema_name='org_reporting', slug='org-reporting')
        self.other = Organization(name='Other Clinic', schema_name='org_reporting_other', slug='org-reporting-other')
        Organization.objects.bulk_create([self.org, self.other])
        self.context = tenant_context(self.org.pk)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.admin = User.objects.create_user('report-admin@example.test', role='admin', organization=self.org, external_admin_id=101, timezone='UTC', first_name='Synthetic', last_name='Director')
        self.staff = User.objects.create_user('report-staff@example.test', role='staff', organization=self.org, external_admin_id=101, first_name='Synthetic', last_name='Staff')
        self.client_a = Client.objects.create(first_name='Synthetic', last_name='A', external_id='1001', external_admin_id=101)
        self.client_b = Client.objects.create(first_name='Synthetic', last_name='B', external_id='2001', external_admin_id=202)
        self.request = SimpleNamespace(user=self.admin, tenant=self.org, _jwt_payload={'org_id': self.org.pk})

    def report(self, frm=date(2026, 10, 1), to=date(2026, 11, 4), group='week'):
        return organization_analytics(self.request, frm, to, group)

    def session(self, status='approved', client=None, staff=None, when=None, submitted=None):
        return SessionRun.objects.create(external_client_id=int((client or self.client_a).external_id), staff=staff or self.staff,
                                         status=status, started_at=when or NOW - timedelta(days=2), submitted_at=submitted)

    def target(self, client=None, status='acquisition'):
        program = Program.objects.create(name='Synthetic Program', category='skill_acquisition', external_client_id=(client or self.client_a).pk)
        return Target.objects.create(program=program, name='Synthetic Target', measurement_type='discrete_trial', status=status)

    def test_session_totals_and_approval_denominator(self):
        for status in ('approved', 'approved', 'rejected', 'submitted', 'open'):
            self.session(status)
        report = self.report()
        self.assertEqual(report['sessions']['total'], 5)
        self.assertEqual(report['sessions']['approval_rate'], 66.7)
        self.assertEqual(report['staff'][0]['sessions'], 5)
        self.assertEqual(sum(p['sessions'] for p in report['session_trend']), 5)

    def test_zero_denominators_are_unavailable(self):
        report = self.report()
        self.assertIsNone(report['sessions']['approval_rate'])
        self.assertIsNone(report['mastery']['current_mastery_rate'])
        self.assertEqual(report['sessions']['total'], 0)

    def test_other_practice_excluded_from_every_metric(self):
        self.session(client=self.client_b)
        self.target(client=self.client_b, status='mastered')
        LessonNote.objects.create(external_client_id=2001, status='submitted', note_date=NOW.date(), submitted_at=NOW - timedelta(days=10))
        report = self.report()
        self.assertEqual(report['sessions']['total'], 0)
        self.assertEqual(report['mastery']['total_targets'], 0)
        self.assertEqual(report['pending_review']['notes']['total'], 0)
        self.assertEqual(report['clients']['total'], 1)

    def test_other_organization_excluded(self):
        with tenant_context(self.other.pk):
            client = Client.objects.create(first_name='Foreign', last_name='Synthetic', external_id='3001', external_admin_id=101)
            SessionRun.objects.create(external_client_id=3001, started_at=NOW, status='approved')
            self.target(client=client, status='mastered')
        self.assertEqual(self.report()['sessions']['total'], 0)
        self.assertEqual(self.report()['mastery']['total_targets'], 0)

    def test_native_org_admin_sees_all_organization_practices(self):
        self.admin.external_admin_id = None
        self.admin.save()
        self.session(client=self.client_a)
        self.session(client=self.client_b)
        report = self.report()
        self.assertEqual(report['sessions']['total'], 2)
        self.assertEqual(report['clients']['total'], 2)

    def test_ambiguous_local_external_identifier_fails_closed(self):
        self.client_b.external_id = str(self.client_a.pk)
        self.client_b.save()
        SessionRun.objects.create(external_client_id=self.client_a.pk, started_at=NOW, status='approved')
        report = self.report()
        self.assertEqual(report['sessions']['total'], 0)
        self.assertEqual(report['ambiguous_identifiers_excluded'], 1)

    def test_foreign_staff_name_is_never_returned(self):
        foreign = User.objects.create_user('foreign-reporting@example.test', role='staff', organization=self.other, external_admin_id=101, first_name='DoNotExpose')
        self.session(staff=foreign)
        report = self.report()
        self.assertEqual(report['staff'][0]['name'], 'Unassigned / unavailable')
        self.assertIsNone(report['staff'][0]['staff_id'])
        self.assertNotIn('DoNotExpose', str(report))

    def test_weekly_buckets_use_user_timezone_and_restore_it(self):
        self.admin.timezone = 'America/Los_Angeles'
        self.session(when=datetime(2026, 11, 2, 0, 30, tzinfo=dt_timezone.utc))
        before = timezone.get_current_timezone()
        report = self.report(date(2026, 10, 26))
        self.assertEqual(report['session_trend'][0], {'period': '2026-10-26', 'sessions': 1})
        self.assertEqual(report['session_trend'][1]['sessions'], 0)
        self.assertEqual(timezone.get_current_timezone(), before)

    def test_monthly_buckets_fill_empty_months(self):
        self.session(when=NOW)
        report = self.report(date(2026, 9, 1), group='month')
        self.assertEqual([p['sessions'] for p in report['session_trend']], [0, 0, 1])

    def test_date_end_includes_local_day_not_next_day(self):
        self.session(when=datetime(2026, 11, 3, 23, 59, tzinfo=dt_timezone.utc))
        self.session(when=datetime(2026, 11, 4, 0, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(self.report(to=date(2026, 11, 3))['sessions']['total'], 1)

    def test_pending_aging_includes_old_backlog_and_unknown_dates(self):
        for delta in (timedelta(hours=1), timedelta(days=1), timedelta(days=3), timedelta(days=7)):
            self.session(status='submitted', when=NOW - timedelta(days=100), submitted=NOW - delta)
        self.session(status='submitted', submitted=None)
        self.session(status='submitted', submitted=NOW + timedelta(days=1))
        report = self.report()
        aging = report['pending_review']['sessions']
        self.assertEqual(aging['total'], 6)
        self.assertEqual([aging[k] for k in ('under_1_day', 'days_1_to_3', 'days_3_to_7', 'days_7_plus', 'unknown')], [1, 1, 1, 1, 2])
        self.assertEqual(aging['oldest_wait_days'], 7)

    def test_unique_mastery_transitions_and_reopening(self):
        target = self.target(status='acquisition')
        self.target(status='mastered')
        for _ in range(2):
            TargetStatusChange.objects.create(target=target, from_status='acquisition', to_status='mastered', trigger='manual')
        report = self.report()
        self.assertEqual(report['mastery']['targets_mastered_in_period'], 1)
        self.assertEqual(report['mastery']['clients_with_mastery_in_period'], 1)
        self.assertEqual(report['mastery']['current_mastery_rate'], 50)

    def test_history_before_first_observation_is_unknown(self):
        Client.objects.filter(pk=self.client_a.pk).update(created_at=NOW - timedelta(days=100))
        report = self.report(date(2026, 9, 1), group='month')
        self.assertIsNone(report['client_status_trend'][0]['active'])
        self.assertEqual(report['client_status_trend'][0]['unknown_clients'], 1)
        self.assertEqual(report['client_status_trend'][-1]['active'], 1)

    def test_recorded_status_changes_form_historical_trend(self):
        Client.objects.filter(pk=self.client_a.pk).update(created_at=NOW - timedelta(days=100))
        self.client_a.refresh_from_db()
        ClientStatusObservation.objects.filter(client=self.client_a).update(observed_at=datetime(2026, 9, 1, tzinfo=dt_timezone.utc))
        self.client_a.status = 'inactive'
        self.client_a.save()
        report = self.report(date(2026, 9, 1), group='month')
        self.assertEqual([(p['active'], p['inactive']) for p in report['client_status_trend']], [(1, 0), (1, 0), (0, 1)])

    def test_status_tracking_does_not_duplicate_unchanged_saves(self):
        self.client_a.preferred_name = 'Synthetic Alias'
        self.client_a.save()
        self.assertEqual(ClientStatusObservation.objects.filter(client=self.client_a).count(), 1)

    def test_bulk_sync_status_tracking(self):
        self.client_a.status = 'discharged'
        Client.objects.bulk_update([self.client_a], ['status'])
        record_client_statuses([self.client_a.pk])
        record_client_statuses([self.client_a.pk])
        self.assertEqual(list(ClientStatusObservation.objects.filter(client=self.client_a).order_by('pk').values_list('status', flat=True)), ['active', 'discharged'])

    def test_failed_history_write_rolls_back_client_status(self):
        self.client_a.status = 'inactive'
        with patch('apps.clients.status_history.record_client_statuses', side_effect=RuntimeError('Synthetic history outage')):
            with self.assertRaises(RuntimeError):
                self.client_a.save()
        self.client_a.refresh_from_db()
        self.assertEqual(self.client_a.status, 'active')

    def test_failed_history_write_rolls_back_bulk_sync(self):
        from apps.clients.api import _upsert_clients_from_patients
        fields = {'external_id': '1001', 'external_admin_id': 101, 'first_name': 'Synthetic',
                  'last_name': 'A', 'preferred_name': '', 'date_of_birth': None, 'status': 'inactive', 'is_active': False}
        with patch('apps.clients.api._map_patient_fields', return_value=fields), patch('apps.clients.status_history.record_client_statuses', side_effect=RuntimeError('Synthetic history outage')):
            with self.assertRaises(RuntimeError):
                _upsert_clients_from_patients([{}], fallback_admin_id=101, include_inactive=True, search=None)
        self.client_a.refresh_from_db()
        self.assertEqual(self.client_a.status, 'active')

    def test_staff_reporting_and_caregiver_roles_rejected(self):
        for role in ('staff', 'reporting', 'caregiver'):
            self.request.user = SimpleNamespace(role=role)
            with self.assertRaises(HttpError) as error:
                self.report()
            self.assertEqual(error.exception.status_code, 403)

    def test_disabled_supervisor_dashboard_permission_rejected(self):
        self.admin.role = 'supervisor'
        self.admin.save()
        RolePermission.objects.create(organization=self.org, role='supervisor', permissions={'dashboard': False})
        with self.assertRaises(HttpError):
            self.report()

    def test_supervisor_can_read_authorized_organization_report(self):
        self.admin.role = 'supervisor'
        self.admin.save()
        self.session()
        self.assertEqual(self.report()['sessions']['total'], 1)

    def test_information_only_programs_are_excluded_from_mastery(self):
        target = self.target(status='mastered')
        target.program.category = 'instructions_only'
        target.program.save()
        self.assertEqual(self.report()['mastery']['total_targets'], 0)

    def test_background_tpms_pull_records_status_changes(self):
        from apps.integrations.tpms_pull import pull_clients
        self.org.integration_admin_id = 101
        fields = {'external_id': '1001', 'external_admin_id': 101, 'first_name': 'Synthetic',
                  'last_name': 'A', 'preferred_name': '', 'date_of_birth': None, 'status': 'inactive'}
        with patch('apps.integrations.tpms_pull._with_admin_token', return_value=[{'admin_id': 101}]), patch('apps.integrations.tpms_pull._map_admin_client', return_value=fields):
            pull_clients(self.org)
        self.assertEqual(ClientStatusObservation.objects.filter(client=self.client_a).order_by('-id').first().status, 'inactive')

    def test_interactive_tpms_upsert_records_status_changes(self):
        from apps.clients.api import _upsert_clients_from_patients
        fields = {'external_id': '1001', 'external_admin_id': 101, 'first_name': 'Synthetic',
                  'last_name': 'A', 'preferred_name': '', 'date_of_birth': None, 'status': 'inactive', 'is_active': False}
        with patch('apps.clients.api._map_patient_fields', return_value=fields):
            _upsert_clients_from_patients([{}], fallback_admin_id=101, include_inactive=True, search=None)
        self.assertEqual(ClientStatusObservation.objects.filter(client=self.client_a).order_by('-id').first().status, 'inactive')

    def test_partner_key_context_rejected(self):
        self.request.api_key = object()
        with self.assertRaises(HttpError):
            self.report()

    def test_invalid_ranges_and_grouping_rejected(self):
        cases = [(date(2026, 11, 4), date(2026, 11, 3), 'week'),
                 (date(2024, 1, 1), date(2026, 11, 4), 'week'),
                 (date(2026, 11, 4), date(2026, 11, 5), 'week'),
                 (date(2026, 10, 1), date(2026, 11, 4), 'day')]
        for frm, to, group in cases:
            with self.assertRaises(HttpError):
                self.report(frm, to, group)
