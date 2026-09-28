from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.integrations.tpms_auth_client import TpmsAuthError
from apps.tenants.api import connect_therapy_pms, disconnect_integration, get_integration_settings
from apps.tenants.models import Organization
from apps.tenants.schemas import TherapyPmsConnectRequest


class IntegrationSettingsApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Integ Org', slug='integ-org', schema_name='integ_org')
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
        with mock.patch('apps.integrations.tpms_auth_client.authenticate_admin_raw', side_effect=TpmsAuthError('Invalid email or password')):
            with self.assertRaises(HttpError) as ctx:
                connect_therapy_pms(self._request(), TherapyPmsConnectRequest(email='a@b.com', password='wrong'))
        # Must not be 401 — that status triggers the web app's automatic-logout
        # interceptor, which would sign the admin out of DCM over a TherapyPMS error.
        self.assertNotEqual(ctx.exception.status_code, 401)
        self.assertIn('TherapyPMS', str(ctx.exception))
        self.assertEqual(Organization.objects.get(pk=self.org.pk).integration_platform, '')

    def test_connect_sets_the_platform_on_success(self):
        with mock.patch('apps.integrations.tpms_auth_client.authenticate_admin_raw', return_value={'status': 'ok'}):
            connect_therapy_pms(self._request(), TherapyPmsConnectRequest(email='a@b.com', password='right'))
        self.assertEqual(Organization.objects.get(pk=self.org.pk).integration_platform, 'therapy_pms')

    def test_disconnect_clears_the_platform(self):
        with mock.patch('apps.integrations.tpms_auth_client.authenticate_admin_raw', return_value={'status': 'ok'}):
            connect_therapy_pms(self._request(), TherapyPmsConnectRequest(email='a@b.com', password='right'))
        disconnect_integration(self._request())
        self.assertEqual(Organization.objects.get(pk=self.org.pk).integration_platform, '')
