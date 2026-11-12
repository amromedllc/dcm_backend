"""Superadmin connection management with synthetic mappings; no live services."""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from ninja.errors import HttpError

from apps.tenants.api import (
    connect_superadmin_facility_integration as connect,
    disconnect_superadmin_facility_integration as disconnect,
    list_superadmin_facility_integrations as list_facilities,
)
from apps.tenants.models import OrganizationTpmsAdminId
from apps.tenants.schemas import TherapyPmsConnectRequest, SuperadminFacilityIntegrationSchema


class SuperadminFacilityIntegrationTests(SimpleTestCase):
    def setUp(self):
        self.org = SimpleNamespace(pk=9, id=9, name='Synthetic Clinic', is_active=True,
                                   tpms_admin_ids=mock.Mock())
        self.facility = SimpleNamespace(
            pk=31, id=31, organization=self.org, organization_id=9, admin_id=702,
            facility_name='South', integration_platform='', integration_email='',
            integration_password_encrypted='', integration_admin_id=None,
            last_appointment_sync_at=None, save=mock.Mock(),
        )
        self.org.tpms_admin_ids.filter.return_value.first.return_value = self.facility
        # The target organization intentionally differs from the current tenant.
        self.request = SimpleNamespace(user=SimpleNamespace(pk=8, is_superuser=True),
                                       tenant=SimpleNamespace(pk=1))
        self.data = TherapyPmsConnectRequest(email='south@example.test', password='synthetic-password')

    def test_all_endpoints_reject_non_superadmins_before_lookup(self):
        for role in ('admin', 'supervisor', 'staff', 'caregiver', 'reporting'):
            request = SimpleNamespace(user=SimpleNamespace(is_superuser=False, role=role))
            for function, args in ((list_facilities, ()), (connect, (31, self.data)), (disconnect, (31,))):
                with self.subTest(role=role, endpoint=function.__name__):
                    with mock.patch('apps.tenants.api.OrganizationTpmsAdminId.objects.select_related') as lookup:
                        with self.assertRaises(HttpError) as error:
                            function(request, *args)
                        self.assertEqual(error.exception.status_code, 403)
                        lookup.assert_not_called()

    def test_list_has_safe_fields_without_credentials(self):
        self.facility.integration_password_encrypted = 'synthetic-ciphertext'
        with mock.patch('apps.tenants.api.OrganizationTpmsAdminId.objects.select_related') as mappings:
            mappings.return_value.order_by.return_value = [self.facility]
            result = list_facilities(self.request)
        parsed = SuperadminFacilityIntegrationSchema(**result[0]).model_dump()
        self.assertEqual(parsed['organization_id'], 9)
        self.assertEqual(parsed['admin_id'], 702)
        self.assertEqual(set(parsed), {
            'id', 'organization_id', 'organization_name', 'organization_active',
            'admin_id', 'facility_name', 'connected', 'integration_email', 'last_appointment_sync_at',
        })
        self.assertNotIn('synthetic-ciphertext', str(result))

    def test_connect_pins_selected_facility_and_encrypts_credentials(self):
        session = SimpleNamespace(admin_id=702, access_token='synthetic-token')
        with (
            mock.patch('apps.tenants.api.OrganizationTpmsAdminId.objects.select_related') as mappings,
            mock.patch('apps.integrations.tpms_pull._authenticate_admin_session', return_value=session),
            mock.patch('apps.integrations.tpms_pull.encrypt_password', return_value='synthetic-ciphertext') as encrypt,
            mock.patch('apps.integrations.tpms_pull.store_org_tpms_admin_token') as cache,
            mock.patch('shared.audit.log_event') as audit,
        ):
            mappings.return_value.get.return_value = self.facility
            result = connect(self.request, 31, self.data)
        mappings.return_value.get.assert_called_once_with(pk=31)
        self.org.tpms_admin_ids.filter.assert_called_once_with(admin_id=702)
        encrypt.assert_called_once_with('synthetic-password')
        self.assertEqual(self.facility.integration_password_encrypted, 'synthetic-ciphertext')
        cache.assert_called_once_with('9:facility:702', 'synthetic-token')
        self.facility.save.assert_called_once()
        self.assertTrue(result['connected'])
        for secret in ('synthetic-password', 'synthetic-ciphertext', 'synthetic-token'):
            self.assertNotIn(secret, str(result))
            self.assertNotIn(secret, str(audit.call_args))
        self.assertEqual(audit.call_args.args[1], 8)

    def test_wrong_facility_credentials_do_not_mutate_connection(self):
        for wrong_admin in (701, 999):
            with self.subTest(wrong_admin=wrong_admin):
                with (
                    mock.patch('apps.tenants.api.OrganizationTpmsAdminId.objects.select_related') as mappings,
                    mock.patch('apps.integrations.tpms_pull._authenticate_admin_session', return_value=SimpleNamespace(admin_id=wrong_admin)),
                    mock.patch('apps.integrations.tpms_pull.encrypt_password') as encrypt,
                    mock.patch('apps.integrations.tpms_pull.store_org_tpms_admin_token') as cache,
                ):
                    mappings.return_value.get.return_value = self.facility
                    with self.assertRaises(HttpError) as error:
                        connect(self.request, 31, self.data)
                    self.assertEqual(error.exception.status_code, 400)
                    encrypt.assert_not_called()
                    cache.assert_not_called()
                    self.facility.save.assert_not_called()
                    self.assertEqual(self.facility.integration_platform, '')

    def test_failed_remote_login_preserves_existing_connection(self):
        self.facility.integration_platform = 'therapy_pms'
        self.facility.integration_password_encrypted = 'old-synthetic-ciphertext'
        with (
            mock.patch('apps.tenants.api.OrganizationTpmsAdminId.objects.select_related') as mappings,
            mock.patch('apps.integrations.tpms_pull._authenticate_admin_session', side_effect=HttpError(400, 'TherapyPMS login failed')),
            mock.patch('shared.audit.log_event') as audit,
        ):
            mappings.return_value.get.return_value = self.facility
            with self.assertRaises(HttpError):
                connect(self.request, 31, self.data)
        self.facility.save.assert_not_called()
        self.assertEqual(self.facility.integration_password_encrypted, 'old-synthetic-ciphertext')
        self.assertEqual(self.facility.integration_platform, 'therapy_pms')
        audit.assert_not_called()

    def test_missing_mapping_returns_404_without_remote_authentication(self):
        with (
            mock.patch('apps.tenants.api.OrganizationTpmsAdminId.objects.select_related') as mappings,
            mock.patch('apps.integrations.tpms_pull._authenticate_admin_session') as authenticate,
        ):
            mappings.return_value.get.side_effect = OrganizationTpmsAdminId.DoesNotExist
            with self.assertRaises(HttpError) as error:
                connect(self.request, 999, self.data)
        self.assertEqual(error.exception.status_code, 404)
        authenticate.assert_not_called()

    def test_blank_credentials_do_not_call_therapypms(self):
        with (
            mock.patch('apps.tenants.api.OrganizationTpmsAdminId.objects.select_related') as mappings,
            mock.patch('apps.integrations.tpms_pull._authenticate_admin_session') as authenticate,
        ):
            mappings.return_value.get.return_value = self.facility
            with self.assertRaises(HttpError) as error:
                connect(self.request, 31, TherapyPmsConnectRequest(email=' ', password=''))
        self.assertEqual(error.exception.status_code, 400)
        authenticate.assert_not_called()

    def test_disconnect_clears_only_selected_facility_and_preserves_checkpoint(self):
        checkpoint = object()
        self.facility.last_appointment_sync_at = checkpoint
        self.facility.integration_platform = 'therapy_pms'
        self.facility.integration_email = 'south@example.test'
        self.facility.integration_password_encrypted = 'synthetic-ciphertext'
        self.facility.integration_admin_id = 702
        with (
            mock.patch('apps.tenants.api.OrganizationTpmsAdminId.objects.select_related') as mappings,
            mock.patch('apps.integrations.tpms_pull.clear_org_tpms_admin_token') as cache,
            mock.patch('shared.audit.log_event') as audit,
        ):
            mappings.return_value.get.return_value = self.facility
            self.assertEqual(disconnect(self.request, 31), (204, None))
        cache.assert_called_once_with('9:facility:702')
        self.assertEqual(self.facility.integration_platform, '')
        self.assertEqual(self.facility.integration_password_encrypted, '')
        self.assertIsNone(self.facility.integration_admin_id)
        self.assertIs(self.facility.last_appointment_sync_at, checkpoint)
        self.facility.save.assert_called_once()
        audit.assert_called_once()
