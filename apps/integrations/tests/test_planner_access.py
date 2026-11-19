"""Synthetic gate tests; no vendor keys, Redis traffic or external HTTP."""
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.test import TestCase, override_settings
from ninja.errors import HttpError
from ninja.testing import TestClient

from apps.accounts.auth import create_access_token
from apps.accounts.models import User
from apps.integrations.practice_planner.access import availability_for_request, provider_status
from apps.integrations.practice_planner.api import router
from apps.integrations.practice_planner.contracts import PlannerUnavailable
from apps.integrations.practice_planner.service import service_for_user
from apps.tenants.api import (
    create_superadmin_tpms_admin_id, update_superadmin_practice_email_setting,
    get_superadmin_practice_planner_status, list_superadmin_practice_email_settings,
)
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from apps.tenants.schemas import TpmsAdminEmailSettingUpdate, TpmsAdminIdCreate
from shared.tenancy import tenant_context


@override_settings(DEBUG=True, PRACTICE_PLANNER_PROVIDER='mock')
class PlannerAccessTests(TestCase):
    def setUp(self):
        self.org, self.other_org = Organization.objects.bulk_create([
            Organization(name='Synthetic A', slug='access-a', schema_name='access_a'),
            Organization(name='Synthetic B', slug='access-b', schema_name='access_b'),
        ])
        self.context = tenant_context(self.org.pk)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.facility = OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=901)
        self.other = OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=902)
        self.foreign = OrganizationTpmsAdminId.objects.create(organization=self.other_org, admin_id=903)
        self.superadmin = User.objects.create(email='synthetic-super@example.test', role='admin', is_superuser=True, organization=self.org)
        self.staff = User.objects.create(email='synthetic-staff@example.test', role='staff', external_admin_id=901)

    def request(self, user=None, tenant=None):
        user = user or self.staff
        tenant = tenant or self.org
        return SimpleNamespace(user=user, tenant=tenant,
            _jwt_payload={'sub': str(user.pk), 'org_id': tenant.pk, 'type': 'access'})

    def change(self, facility=None, user=None, **changes):
        return update_superadmin_practice_email_setting(self.request(user or self.superadmin),
            (facility or self.facility).pk, TpmsAdminEmailSettingUpdate(**changes))

    def enable(self, facility=None):
        return self.change(facility, wiley_enabled=True)

    def assert_denied(self, request=None):
        request = request or self.request()
        self.assertFalse(availability_for_request(request)['enabled'])
        with self.assertRaises(PermissionDenied):
            service_for_user(request)

    def test_default_off_and_exact_facility_only(self):
        self.assertFalse(self.facility.wiley_enabled)
        self.assertFalse(self.facility.wiley_entitled)
        self.assert_denied()
        self.enable(self.other)
        self.assert_denied()
        self.enable()
        self.assertTrue(availability_for_request(self.request())['enabled'])
        self.assertTrue(service_for_user(self.request()).get_component('p1', edition='test-1'))

    def test_verified_tenant_supports_nullable_user_organization(self):
        self.enable()
        self.assertIsNone(self.staff.organization_id)
        self.assertTrue(availability_for_request(self.request())['enabled'])
        self.staff.organization = self.other_org
        self.staff.save()
        self.assert_denied()

    def test_tenant_token_context_and_mapping_must_agree(self):
        self.enable()
        request = self.request()
        request._jwt_payload['org_id'] = self.other_org.pk
        self.assert_denied(request)
        self.assert_denied(self.request(tenant=self.other_org))
        with tenant_context(self.other_org.pk):
            self.assert_denied()
        self.staff.external_admin_id = 903
        self.staff.save()
        self.enable(self.foreign)
        self.assert_denied()

    def test_missing_native_unmapped_and_partner_sessions_denied(self):
        self.enable()
        for admin_id in (None, 999):
            User.objects.filter(pk=self.staff.pk).update(external_admin_id=admin_id)
            self.assert_denied()
        request = self.request()
        request.api_key = object()
        self.assert_denied(request)
        self.assert_denied(SimpleNamespace())
        request = self.request()
        request._jwt_payload['sub'] = str(self.superadmin.pk)
        self.assert_denied(request)

    def test_roles_and_deactivation_rechecked_from_database(self):
        self.enable()
        for role in ('admin', 'supervisor', 'staff'):
            User.objects.filter(pk=self.staff.pk).update(role=role)
            self.assertTrue(availability_for_request(self.request())['enabled'])
        for role in ('caregiver', 'reporting'):
            User.objects.filter(pk=self.staff.pk).update(role=role, external_client_id=100 if role == 'caregiver' else None)
            self.assert_denied()
        User.objects.filter(pk=self.staff.pk).update(role='staff', external_client_id=None, is_active=False)
        self.assert_denied()
        User.objects.filter(pk=self.staff.pk).update(is_active=True)
        Organization.objects.filter(pk=self.org.pk).update(is_active=False)
        self.assert_denied()

    def test_all_non_superadmin_roles_rejected_on_settings_and_status(self):
        for role in ('admin', 'supervisor', 'staff', 'caregiver', 'reporting'):
            self.staff.role = role
            for call in (lambda: self.change(user=self.staff, wiley_enabled=True),
                         lambda: get_superadmin_practice_planner_status(self.request()),
                         lambda: list_superadmin_practice_email_settings(self.request()),
                         lambda: create_superadmin_tpms_admin_id(self.request(), TpmsAdminIdCreate(organization_id=self.org.pk, admin_id=999, wiley_enabled=True))):
                with self.subTest(role=role), self.assertRaises(HttpError) as error:
                    call()
                self.assertEqual(error.exception.status_code, 403)

    def test_mock_needs_no_license_but_production_mock_fails_closed(self):
        self.enable()
        self.facility.refresh_from_db()
        self.assertFalse(self.facility.wiley_entitled)
        with override_settings(DEBUG=False):
            self.assert_denied()
            with self.assertRaises(HttpError):
                self.enable()

    def test_enable_unavailable_provider_rejected_but_disable_and_revoke_work(self):
        self.change(wiley_enabled=True, wiley_entitled=True)
        with override_settings(PRACTICE_PLANNER_PROVIDER='live'):
            with self.assertRaises(HttpError) as error:
                self.enable()
            self.assertEqual(error.exception.status_code, 409)
            self.assert_denied()
            self.change(wiley_entitled=False)
            self.change(wiley_enabled=False)
        self.facility.refresh_from_db()
        self.assertFalse(self.facility.wiley_enabled)
        self.assertFalse(self.facility.wiley_entitled)

    def test_live_entitlement_validation_and_revocation(self):
        # Synthetic readiness stand-in, never a real vendor or HTTP adapter.
        ready = {'provider': 'wiley', 'ready': True, 'is_mock': False, 'reason': 'ready'}
        with patch('apps.integrations.practice_planner.access.provider_status', return_value=ready):
            with self.assertRaises(HttpError):
                self.enable()
            self.change(wiley_enabled=True, wiley_entitled=True)
            self.assertTrue(availability_for_request(self.request())['enabled'])
            self.change(wiley_entitled=False)
            self.assert_denied()

    def test_feature_change_audit_after_commit_is_allowlisted_and_rollback_safe(self):
        with patch('shared.audit.log_event') as audit:
            with self.captureOnCommitCallbacks(execute=True):
                self.enable()
            args = audit.call_args.args
            self.assertEqual(args[:4], ('wiley_feature_changed', self.superadmin.pk, 'facility', self.facility.pk))
            self.assertEqual(args[4], {'organization_id': self.org.pk,
                'previous': {'enabled': False, 'entitled': False}, 'current': {'enabled': True, 'entitled': False}})
            audit.reset_mock()
            with self.captureOnCommitCallbacks(execute=True):
                self.enable()
            audit.assert_not_called()
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        self.change(wiley_enabled=False)
                        raise RuntimeError('Synthetic rollback')
                except RuntimeError:
                    pass
            audit.assert_not_called()
            self.facility.refresh_from_db()
            self.assertTrue(self.facility.wiley_enabled)

    def test_setting_updates_preserve_unrelated_features(self):
        self.change(ai_enabled=True, email_notifications_enabled=True)
        self.enable()
        self.facility.refresh_from_db()
        self.other.refresh_from_db()
        self.assertTrue(self.facility.ai_enabled)
        self.assertTrue(self.facility.email_notifications_enabled)
        self.assertFalse(self.other.wiley_enabled)

    def test_enablement_and_license_cannot_follow_a_remapped_facility(self):
        self.change(wiley_enabled=True, wiley_entitled=True)
        with self.assertRaises(HttpError) as error:
            self.change(admin_id=999)
        self.assertEqual(error.exception.status_code, 409)
        self.facility.refresh_from_db()
        self.facility.organization = self.other_org
        with self.assertRaises(ValidationError):
            self.facility.save()
        self.change(wiley_enabled=False, wiley_entitled=False)
        self.change(admin_id=999)
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.admin_id, 999)
        self.assertFalse(self.facility.wiley_enabled)

    def test_creation_defaults_and_explicit_mock_enablement(self):
        _, data = create_superadmin_tpms_admin_id(self.request(self.superadmin), TpmsAdminIdCreate(organization_id=self.org.pk, admin_id=905))
        self.assertFalse(data['wiley_enabled'])
        _, data = create_superadmin_tpms_admin_id(self.request(self.superadmin), TpmsAdminIdCreate(organization_id=self.org.pk, admin_id=906, wiley_enabled=True))
        self.assertTrue(data['wiley_enabled'])
        self.assertFalse(data['wiley_entitled'])

    def test_provider_status_never_echoes_configuration(self):
        with override_settings(PRACTICE_PLANNER_PROVIDER='synthetic-private-invalid-value'):
            result = provider_status()
        self.assertIsNone(result['provider'])
        self.assertNotIn('synthetic-private-invalid-value', str(result))

    def test_features_response_preserves_ai_and_rechecks_planner_availability(self):
        from apps.accounts.api import get_features
        with patch('shared.ai_client.is_enabled', return_value=True):
            self.assertEqual(get_features(self.request())['ai_enabled'], True)
            self.assertFalse(get_features(self.request())['practice_planner']['enabled'])
            self.enable()
            self.assertTrue(get_features(self.request())['practice_planner']['enabled'])
            self.change(wiley_enabled=False)
            self.assertFalse(get_features(self.request())['practice_planner']['enabled'])

    def test_revocation_blocks_next_capabilities_request(self):
        self.enable()
        client = TestClient(router)
        token = create_access_token(self.staff, self.org.pk)
        with patch('apps.accounts.auth.is_token_blocked', return_value=False):
            headers = {'Authorization': f'Bearer {token}'}
            self.assertEqual(client.get('/capabilities', headers=headers, tenant=self.org).status_code, 200)
            self.change(wiley_enabled=False)
            self.assertEqual(client.get('/capabilities', headers=headers, tenant=self.org).status_code, 403)

    def test_anonymous_and_api_key_cannot_authenticate_to_capabilities(self):
        client = TestClient(router)
        self.assertEqual(client.get('/capabilities', tenant=self.org).status_code, 401)
        self.assertEqual(client.get('/capabilities', headers={'X-API-Key': 'synthetic-key'}, tenant=self.org).status_code, 401)

    def test_provider_failure_returns_sanitized_capabilities_error(self):
        self.enable()
        from apps.integrations.practice_planner.api import capabilities
        with patch('apps.integrations.practice_planner.service.get_practice_planner_service', side_effect=PlannerUnavailable('Synthetic internal detail')):
            with self.assertRaises(HttpError) as error:
                capabilities(self.request())
        self.assertEqual(error.exception.status_code, 503)
        self.assertNotIn('internal detail', str(error.exception))
