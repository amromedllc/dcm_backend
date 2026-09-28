from datetime import date
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.clients.models import Client
from apps.integrations.tpms_auth_client import TpmsAuthError
from apps.integrations.tpms_pull import PullResult, pull_clients, pull_providers
from apps.sessions.models import Appointment
from apps.tenants.api import (
    connect_therapy_pms,
    disconnect_integration,
    get_integration_settings,
    pull_therapy_pms_appointments,
    pull_therapy_pms_clients,
    pull_therapy_pms_providers,
)
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from apps.tenants.schemas import TherapyPmsConnectRequest, TherapyPmsPullAppointmentsRequest
from shared.tenancy import tenant_context


ADMIN_LOGIN_OK = {
    'status': 'success',
    'access_token': 'admin-token-abc',
    'id': 42,
    'email': 'a@b.com',
}


def _patch_admin_login(return_value=ADMIN_LOGIN_OK, side_effect=None):
    kwargs = {'side_effect': side_effect} if side_effect is not None else {'return_value': return_value}
    return mock.patch('apps.integrations.tpms_pull.authenticate_admin_raw', **kwargs)


class IntegrationSettingsApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Integ Org', slug='integ-org', schema_name='integ_org')
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=42, facility_name='Main')
        self.admin = User.objects.create_user(
            email='integ-admin@example.com', password='x', organization=self.org, role=User.Role.ADMIN,
        )

    def _request(self):
        return SimpleNamespace(user=self.admin, tenant=self.org)

    def test_platform_is_blank_until_connected(self):
        result = get_integration_settings(self._request())
        self.assertEqual(result['integration_platform'], '')

    def test_connect_requires_email_and_password(self):
        with self.assertRaises(HttpError) as ctx:
            connect_therapy_pms(self._request(), TherapyPmsConnectRequest(email='', password=''))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_connect_surfaces_a_fixed_message_on_bad_credentials_without_a_401(self):
        with _patch_admin_login(side_effect=TpmsAuthError('Invalid email or password')):
            with self.assertRaises(HttpError) as ctx:
                connect_therapy_pms(self._request(), TherapyPmsConnectRequest(email='a@b.com', password='wrong'))
        self.assertNotEqual(ctx.exception.status_code, 401)
        self.assertIn('TherapyPMS', str(ctx.exception))
        self.assertEqual(Organization.objects.get(pk=self.org.pk).integration_platform, '')

    def test_connect_rejects_unmapped_practice(self):
        payload = {**ADMIN_LOGIN_OK, 'id': 999}
        with _patch_admin_login(return_value=payload):
            with mock.patch('apps.integrations.tpms_pull.store_org_tpms_admin_token'):
                with self.assertRaises(HttpError) as ctx:
                    connect_therapy_pms(
                        self._request(),
                        TherapyPmsConnectRequest(email='a@b.com', password='right'),
                    )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn('not mapped', str(ctx.exception).lower())
        self.assertEqual(Organization.objects.get(pk=self.org.pk).integration_platform, '')

    def test_connect_stores_encrypted_credentials_and_practice_binding(self):
        with _patch_admin_login():
            with mock.patch('apps.integrations.tpms_pull.store_org_tpms_admin_token') as store_token:
                connect_therapy_pms(
                    self._request(),
                    TherapyPmsConnectRequest(email='a@b.com', password='right'),
                )
        org = Organization.objects.get(pk=self.org.pk)
        self.assertEqual(org.integration_platform, 'therapy_pms')
        self.assertEqual(org.integration_email, 'a@b.com')
        self.assertEqual(org.integration_admin_id, 42)
        self.assertTrue(org.integration_password_encrypted)
        self.assertNotIn('right', org.integration_password_encrypted)
        store_token.assert_called_once_with(org.id, 'admin-token-abc')

    def test_disconnect_clears_the_platform_and_credentials(self):
        with _patch_admin_login():
            with mock.patch('apps.integrations.tpms_pull.store_org_tpms_admin_token'):
                connect_therapy_pms(
                    self._request(),
                    TherapyPmsConnectRequest(email='a@b.com', password='right'),
                )
        with mock.patch('apps.integrations.tpms_pull.clear_org_tpms_admin_token') as clear_token:
            disconnect_integration(self._request())
        org = Organization.objects.get(pk=self.org.pk)
        self.assertEqual(org.integration_platform, '')
        self.assertEqual(org.integration_email, '')
        self.assertEqual(org.integration_password_encrypted, '')
        self.assertIsNone(org.integration_admin_id)
        clear_token.assert_called_once_with(org.id)


class TherapyPmsPullIsolationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Pull Org', slug='pull-org', schema_name='pull_org')
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=42)
        self.other = Organization.objects.create(name='Other Org', slug='other-org', schema_name='other_org')
        OrganizationTpmsAdminId.objects.create(organization=self.other, admin_id=99)
        self.admin = User.objects.create_user(
            email='pull-admin@example.com', password='x', organization=self.org, role=User.Role.ADMIN,
        )
        self.org.integration_platform = Organization.IntegrationPlatform.THERAPY_PMS
        self.org.integration_email = 'a@b.com'
        from apps.integrations.tpms_credentials import encrypt_password
        self.org.integration_password_encrypted = encrypt_password('secret')
        self.org.integration_admin_id = 42
        self.org.save()

    def _request(self):
        return SimpleNamespace(user=self.admin, tenant=self.org)

    def _connect_mocks(self):
        return mock.patch(
            'apps.integrations.tpms_pull.get_org_tpms_admin_token',
            return_value='cached-token',
        )

    def test_pull_clients_tags_practice_and_skips_other_facility_rows(self):
        rows = [
            {'id': 1, 'client_first_name': 'Ada', 'client_last_name': 'Lovelace', 'admin_id': 42},
            {'id': 2, 'client_first_name': 'Other', 'client_last_name': 'Facility', 'admin_id': 99},
        ]
        with self._connect_mocks():
            with mock.patch('apps.integrations.tpms_pull.admin_list_clients', return_value=rows):
                with schema_context(self.org.schema_name), tenant_context(self.org.id):
                    result = pull_clients(self.org)

        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped, 1)
        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            clients = list(Client.objects.all())
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].external_id, '1')
        self.assertEqual(clients[0].external_admin_id, 42)
        self.assertEqual(clients[0].organization_id, self.org.id)

    def test_pull_providers_does_not_steal_other_org_email(self):
        User.objects.create_user(
            email='shared@example.com',
            password='x',
            organization=self.other,
            role=User.Role.STAFF,
            external_admin_id=99,
            external_employee_id=7,
        )
        rows = [
            {
                'id': 7,
                'login_email': 'shared@example.com',
                'first_name': 'Taken',
                'last_name': 'Elsewhere',
                'admin_id': 42,
            },
            {
                'id': 8,
                'login_email': 'new.provider@example.com',
                'first_name': 'New',
                'last_name': 'Provider',
                'admin_id': 42,
            },
        ]
        with self._connect_mocks():
            with mock.patch('apps.integrations.tpms_pull.admin_list_providers', return_value=rows):
                with schema_context(self.org.schema_name), tenant_context(self.org.id):
                    result = pull_providers(self.org)

        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped, 1)
        stolen = User.objects.get(email='shared@example.com')
        self.assertEqual(stolen.organization_id, self.other.id)
        self.assertEqual(stolen.external_admin_id, 99)
        created = User.objects.get(email='new.provider@example.com')
        self.assertEqual(created.organization_id, self.org.id)
        self.assertEqual(created.external_admin_id, 42)
        self.assertEqual(created.external_employee_id, 8)

    def test_pull_appointments_endpoint_requires_dates_and_upserts(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            Client.objects.create(
                first_name='Ada',
                last_name='Lovelace',
                external_id='10',
                external_admin_id=42,
                organization=self.org,
            )
            staff = User.objects.create_user(
                email='staff@example.com',
                password='x',
                organization=self.org,
                role=User.Role.STAFF,
                external_admin_id=42,
                external_employee_id=5,
            )

        rows = [
            {
                'id': 100,
                'client_id': 10,
                'provider_id': 5,
                'admin_id': 42,
                'from_time': '2026-09-01 10:00:00',
                'to_time': '2026-09-01 11:00:00',
                'activity_type': 'ABA',
                'status': 'Scheduled',
            },
            {
                'id': 101,
                'client_id': 10,
                'provider_id': 5,
                'admin_id': 99,  # other facility — skipped
                'from_time': '2026-09-02 10:00:00',
                'to_time': '2026-09-02 11:00:00',
                'status': 'Scheduled',
            },
        ]

        with self._connect_mocks():
            with mock.patch('apps.integrations.tpms_pull.admin_list_appointments', return_value=rows):
                with schema_context(self.org.schema_name), tenant_context(self.org.id):
                    result = pull_therapy_pms_appointments(
                        self._request(),
                        TherapyPmsPullAppointmentsRequest(
                            from_date=date(2026, 9, 1),
                            to_date=date(2026, 9, 30),
                        ),
                    )

        self.assertEqual(result['created'], 1)
        self.assertEqual(result['skipped'], 1)
        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            appts = list(Appointment.objects.all())
        self.assertEqual(len(appts), 1)
        self.assertEqual(appts[0].external_id, '100')
        self.assertEqual(appts[0].external_client_id, 10)
        self.assertEqual(appts[0].staff_id, staff.id)
        self.assertEqual(appts[0].source, Appointment.Source.SYNCED)
        self.assertEqual(appts[0].organization_id, self.org.id)

    def test_pull_clients_endpoint_requires_connection(self):
        self.org.integration_platform = ''
        self.org.save(update_fields=['integration_platform'])
        with self.assertRaises(HttpError) as ctx:
            pull_therapy_pms_clients(self._request())
        self.assertEqual(ctx.exception.status_code, 400)

    def test_pull_providers_endpoint_wired(self):
        with self._connect_mocks():
            with mock.patch(
                'apps.integrations.tpms_pull.pull_providers',
                return_value=PullResult(created=2, updated=1),
            ) as mocked:
                result = pull_therapy_pms_providers(self._request())
        mocked.assert_called_once()
        self.assertEqual(result['created'], 2)
        self.assertEqual(result['updated'], 1)
