from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django_tenants.utils import schema_context
import os
from unittest import skipUnless
from importlib import import_module
from django.apps import apps
from ninja.errors import HttpError

from apps.integrations.models import PullJob
from apps.integrations.tpms_credentials import decrypt_password
from apps.integrations.tpms_pull import FacilityConnection, _admin_access_token
from apps.tenants.api import (
    connect_therapy_pms, disconnect_integration, get_integration_settings,
    list_pull_jobs, pull_therapy_pms_providers,
)
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from apps.tenants.schemas import TherapyPmsConnectRequest
from shared.tenancy import tenant_context


class FacilityIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization(name='Multi Facility', slug='multi-facility', schema_name='multi_facility')
        cls.org.save()
        cls.a = OrganizationTpmsAdminId.objects.create(organization=cls.org, admin_id=701, facility_name='North')
        cls.b = OrganizationTpmsAdminId.objects.create(organization=cls.org, admin_id=702, facility_name='South')

    def setUp(self):
        self.request = SimpleNamespace(tenant=self.org, user=mock.Mock(has_role=mock.Mock(return_value=True)))

    def connect(self, admin_id, email, password):
        with (
            mock.patch('apps.integrations.tpms_pull.authenticate_admin_raw', return_value={
                'status': 'success', 'id': admin_id, 'email': email, 'access_token': f'token-{admin_id}',
            }),
            mock.patch('apps.integrations.tpms_pull.store_org_tpms_admin_token') as store,
        ):
            connect_therapy_pms(self.request, TherapyPmsConnectRequest(email=email, password=password), admin_id)
        return store

    def test_connections_and_disconnect_are_independent(self):
        token_a = self.connect(701, 'north@example.com', 'north-secret')
        token_b = self.connect(702, 'south@example.com', 'south-secret')
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(decrypt_password(self.a.integration_password_encrypted), 'north-secret')
        self.assertEqual(decrypt_password(self.b.integration_password_encrypted), 'south-secret')
        self.assertNotEqual(token_a.call_args.args[0], token_b.call_args.args[0])
        with mock.patch('apps.integrations.tpms_pull.clear_org_tpms_admin_token') as clear:
            disconnect_integration(self.request, 701)
        clear.assert_called_once_with(f'{self.org.id}:facility:701')
        self.assertEqual(get_integration_settings(self.request, 701)['integration_platform'], '')
        self.assertEqual(get_integration_settings(self.request, 702)['integration_email'], 'south@example.com')
        self.org.refresh_from_db()
        self.assertEqual(self.org.integration_password_encrypted, '')

    def test_migration_moves_legacy_credentials_only_to_matching_facility(self):
        from apps.integrations.tpms_credentials import encrypt_password
        self.org.integration_platform = 'therapy_pms'
        self.org.integration_admin_id = 701
        self.org.integration_email = 'north@example.com'
        self.org.integration_password_encrypted = encrypt_password('north-secret')
        self.org.save()
        migration = import_module('apps.tenants.migrations.0012_facility_integration_credentials')
        migration.move_connections(apps, None)
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.org.refresh_from_db()
        self.assertEqual(decrypt_password(self.a.integration_password_encrypted), 'north-secret')
        self.assertEqual(self.a.integration_admin_id, 701)
        self.assertEqual(self.b.integration_password_encrypted, '')
        self.assertEqual(self.org.integration_password_encrypted, '')

    def test_wrong_facility_credentials_are_rejected(self):
        with (
            mock.patch('apps.integrations.tpms_pull.authenticate_admin_raw', return_value={
                'status': 'success', 'id': 702, 'email': 'south@example.com', 'access_token': 'south-token',
            }),
            self.assertRaises(HttpError),
        ):
            connect_therapy_pms(self.request, TherapyPmsConnectRequest(email='south@example.com', password='secret'), 701)
        self.a.refresh_from_db()
        self.assertEqual(self.a.integration_email, '')

    def test_other_organizations_facility_is_rejected(self):
        other = Organization(name='Other', slug='other-facility-org', schema_name='other_facility_org')
        other.auto_create_schema = False
        other.save()
        OrganizationTpmsAdminId.objects.create(organization=other, admin_id=999)
        with self.assertRaises(HttpError) as error:
            get_integration_settings(self.request, 999)
        self.assertEqual(error.exception.status_code, 404)

    def test_multi_facility_mutation_requires_selection(self):
        with self.assertRaises(HttpError):
            disconnect_integration(self.request)

    def test_settings_list_facilities_without_secrets(self):
        self.connect(701, 'north@example.com', 'north-secret')
        result = get_integration_settings(self.request)
        self.assertIsNone(result['facility_admin_id'])
        self.assertEqual(len(result['facilities']), 2)
        self.assertNotIn('secret', str(result))

    def test_refresh_uses_selected_facility_credentials_and_token_key(self):
        self.connect(701, 'north@example.com', 'north-secret')
        self.a.refresh_from_db()
        connection = FacilityConnection(self.org, self.a)
        with (
            mock.patch('apps.integrations.tpms_pull.get_org_tpms_admin_token', return_value=None) as cached,
            mock.patch('apps.integrations.tpms_pull.authenticate_admin_raw', return_value={
                'status': 'success', 'id': 701, 'email': 'north@example.com', 'access_token': 'new-token',
            }) as login,
            mock.patch('apps.integrations.tpms_pull.store_org_tpms_admin_token'),
        ):
            self.assertEqual(_admin_access_token(connection), 'new-token')
        cached.assert_called_once_with(f'{self.org.id}:facility:701')
        login.assert_called_once_with('north@example.com', 'north-secret')

    def test_sync_job_pins_facility_and_history_is_filtered(self):
        self.connect(701, 'north@example.com', 'north-secret')
        with schema_context(self.org.schema_name), tenant_context(self.org.id):
            with mock.patch('apps.integrations.tasks.run_pull_job.delay') as queued:
                self.request.user = None
                with mock.patch('apps.tenants.api._require_manager'):
                    code, job = pull_therapy_pms_providers(self.request, 701)
            self.assertEqual(code, 202)
            self.assertEqual(job.params['facility_admin_id'], 701)
            queued.assert_called_once_with(job_id=job.id, org_id=self.org.id)
            PullJob.objects.create(job_type='providers', params={'facility_admin_id': 702})
            with mock.patch('apps.tenants.api._require_manager'):
                history = list_pull_jobs(self.request, facility_admin_id=701)
            self.assertEqual([j.id for j in history], [job.id])

    @skipUnless(os.environ.get('DCM_TPMS_SIMULATION_URL'), 'Requires local Postman TherapyPMS simulation')
    def test_real_http_connect_and_refresh_keep_facilities_separate(self):
        with (
            override_settings(TPMS_API_BASE_URL=os.environ.get('DCM_TPMS_SIMULATION_URL')),
            mock.patch('apps.integrations.tpms_pull.store_org_tpms_admin_token') as store,
            mock.patch('apps.integrations.tpms_pull.get_org_tpms_admin_token', return_value=None),
        ):
            for admin_id, email, password in (
                (701, 'north@example.com', 'north-secret'),
                (702, 'south@example.com', 'south-secret'),
            ):
                connect_therapy_pms(self.request, TherapyPmsConnectRequest(email=email, password=password), admin_id)
                facility = self.org.tpms_admin_ids.get(admin_id=admin_id)
                token = _admin_access_token(FacilityConnection(self.org, facility))
                self.assertEqual(token, f'token-{admin_id}')
            self.assertEqual({call.args[0] for call in store.call_args_list}, {
                f'{self.org.id}:facility:701', f'{self.org.id}:facility:702',
            })
            with self.assertRaises(HttpError):
                connect_therapy_pms(self.request, TherapyPmsConnectRequest(email='south@example.com', password='south-secret'), 701)
