from datetime import date
from unittest import mock
from unittest import skipUnless
from types import SimpleNamespace
import os

from django.test import TestCase, override_settings
from django_tenants.utils import schema_context

from apps.clients.models import Client
from apps.integrations.tpms_pull import pull_appointments
from apps.sessions.models import Appointment
from apps.tenants.models import Organization
from shared.tenancy import tenant_context

PRACTICE_ADMIN_ID = 1

# Verbatim shape of a row from GET /api/v1/admin/get/appointment
SAMPLE_ROW = {
    'id': 2038296,
    'session_title': 'NBC_Regular Time_2026-10-11',
    'admin_id': 1,
    'billable': 2,
    'client_id': 15808,
    'authorization_id': '',
    'authorization_activity_name': 'Regular Time',
    'payor_id': 0,
    'provider_id': 2956,
    'time_duration': 15,
    'cpt_code': '',
    'schedule_date': '2026-10-11',
    'from_time': '2026-10-11T13:15:00.000000Z',
    'to_time': '2026-10-11T13:30:00.000000Z',
    'status': 'Scheduled',
    'created_at': '2026-09-20T13:33:03.000000Z',
    'updated_at': '2026-09-20T13:33:03.000000Z',
    'from_time_timezone': '2026-10-11T07:45:00.000000Z',
    'to_time_timezone': '2026-10-11T08:00:00.000000Z',
}


class PullAppointmentsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Appt Org', slug='appt-org', schema_name='appt_org',
            integration_platform=Organization.IntegrationPlatform.THERAPY_PMS,
            integration_admin_id=PRACTICE_ADMIN_ID,
        )
        self.token_patcher = mock.patch('apps.integrations.tpms_pull._admin_access_token', return_value='fake-token')
        self.token_patcher.start()
        self.addCleanup(self.token_patcher.stop)

    def test_saves_every_field_from_the_real_payload_shape(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                organization=self.org, external_id='15808', external_admin_id=PRACTICE_ADMIN_ID,
                first_name='Pat', last_name='Ient',
            )
            with mock.patch('apps.integrations.tpms_pull.admin_list_appointments', return_value=[SAMPLE_ROW]):
                result = pull_appointments(
                    self.org, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31),
                )

            self.assertEqual(result.created, 1)
            appt = Appointment.objects.get(external_id='2038296')
            self.assertEqual(appt.external_client_id, 15808)
            self.assertEqual(appt.status, Appointment.Status.SCHEDULED)
            self.assertEqual(appt.service_type, 'Regular Time')  # via authorization_activity_name

            self.assertEqual(appt.session_title, 'NBC_Regular Time_2026-10-11')
            self.assertEqual(appt.external_admin_id, PRACTICE_ADMIN_ID)
            self.assertEqual(appt.billable, 2)
            self.assertEqual(appt.authorization_id, '')
            self.assertEqual(appt.payor_id, 0)
            self.assertEqual(appt.time_duration, 15)
            self.assertEqual(appt.cpt_code, '')
            self.assertIsNotNone(appt.external_created_at)
            self.assertIsNotNone(appt.external_updated_at)
            self.assertEqual(appt.start_time_local_raw, '2026-10-11T07:45:00.000000Z')
            self.assertEqual(appt.end_time_local_raw, '2026-10-11T08:00:00.000000Z')

    def test_second_pull_of_the_same_row_does_not_duplicate_the_appointment(self):
        # synced_at always refreshes to "now" on every pull, so an unchanged row
        # still counts as "updated" rather than "skipped" — pre-existing behavior,
        # unrelated to the new fields here. What matters is there's still one row.
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                organization=self.org, external_id='15808', external_admin_id=PRACTICE_ADMIN_ID,
                first_name='Pat', last_name='Ient',
            )
            with mock.patch('apps.integrations.tpms_pull.admin_list_appointments', return_value=[SAMPLE_ROW]):
                pull_appointments(self.org, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31))
                pull_appointments(self.org, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31))

            self.assertEqual(Appointment.objects.filter(external_id='2038296').count(), 1)

    def test_zoom_link_is_saved_updated_cleared_and_exposed_by_api(self):
        from apps.sessions.schemas import AppointmentSchema
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(organization=self.org, external_id='15808', external_admin_id=PRACTICE_ADMIN_ID, first_name='Pat', last_name='Ient')
            for link, expected in (
                ('https://meet.example.com/room-one', 'https://meet.example.com/room-one'),
                ('https://meet.example.com/room-two', 'https://meet.example.com/room-two'),
                ('', ''),
                (True, ''),
                ('javascript:alert(1)', ''),
            ):
                with self.subTest(link=link):
                    with mock.patch('apps.integrations.tpms_pull.admin_list_appointments', return_value=[{**SAMPLE_ROW, 'zoom_link': link}]):
                        pull_appointments(self.org, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31))
                    appt = Appointment.objects.get(external_id=str(SAMPLE_ROW['id']))
                    self.assertEqual(appt.telehealth_link, expected)
                    self.assertEqual(AppointmentSchema.from_orm(appt).telehealth_link, expected)
            self.assertEqual(Appointment.objects.count(), 1)

    def test_session_linked_to_zoom_appointment_defaults_to_telehealth(self):
        from apps.accounts.models import User
        from apps.sessions.api import start_session
        from apps.sessions.schemas import SessionStartRequest
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = Client.objects.create(organization=self.org, external_id='15808', external_admin_id=PRACTICE_ADMIN_ID, first_name='Pat', last_name='Ient')
            staff = User.objects.create_user(email='telehealth-admin@example.com', password='x', role=User.Role.ADMIN, organization=self.org)
            row = {**SAMPLE_ROW, 'zoom_link': 'https://meet.example.com/room-one'}
            with mock.patch('apps.integrations.tpms_pull.admin_list_appointments', return_value=[row]):
                pull_appointments(self.org, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31))
            with (
                mock.patch('apps.sessions.api._accessible_external_client_ids', return_value={client.pk}),
                mock.patch('apps.sessions.api.build_program_snapshot', return_value={}),
            ):
                _, result = start_session(SimpleNamespace(user=staff, tenant=self.org), SessionStartRequest(client_id=client.pk, external_appointment_id=str(SAMPLE_ROW['id'])))
            self.assertEqual(result['service_setting'], 'Telehealth')

    @skipUnless(os.environ.get('DCM_TPMS_APPOINTMENTS_SIMULATION_URL'), 'Requires local appointment HTTP fixture')
    def test_real_http_requests_zoom_link_and_persists_it(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(organization=self.org, external_id='15808', external_admin_id=PRACTICE_ADMIN_ID, first_name='Pat', last_name='Ient')
            with override_settings(TPMS_API_BASE_URL=os.environ['DCM_TPMS_APPOINTMENTS_SIMULATION_URL']):
                pull_appointments(self.org, from_date=date(2026, 10, 1), to_date=date(2026, 10, 31))
            self.assertEqual(Appointment.objects.get(external_id=str(SAMPLE_ROW['id'])).telehealth_link, 'https://meet.example.com/appointment-room')
