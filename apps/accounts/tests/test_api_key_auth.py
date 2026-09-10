"""
Partner API-key auth: the failure modes that actually matter are a key
reaching another organization's data, and a read-only key being able to
mutate. Everything here exists to pin those down.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.auth import APIKeyAuth
from apps.accounts.models import APIKey, User
from apps.tenants.api import (
    create_superadmin_api_key,
    list_superadmin_api_keys,
    revoke_superadmin_api_key,
)
from apps.tenants.models import Organization
from apps.tenants.schemas import SuperadminAPIKeyCreate
from ninja.errors import HttpError
from shared.middleware import _org_id_from_api_key


class FakeRequest:
    def __init__(self, method='GET', tenant=None, api_key_header=None, user=None):
        self.method = method
        self.tenant = tenant
        self.user = user
        self.META = {}
        if api_key_header is not None:
            self.META['HTTP_X_API_KEY'] = api_key_header


class APIKeyModelTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Key Org', slug='key-org', schema_name='key_org',
        )
        self.admin = User.objects.create(
            email='admin@key-org.test', role=User.Role.ADMIN,
            organization=self.org, external_admin_id=777,
        )

    def test_generate_creates_scoped_service_user(self):
        key, raw = APIKey.generate(
            'Partner A', self.admin, organization_id=self.org.id,
            external_admin_id=self.admin.external_admin_id,
        )
        self.assertTrue(raw.startswith('dcm_'))
        self.assertEqual(key.organization_id, self.org.id)
        self.assertEqual(key.external_admin_id, 777)
        self.assertFalse(key.can_write)
        su = key.service_user
        self.assertIsNotNone(su)
        self.assertEqual(su.organization_id, self.org.id)
        self.assertEqual(su.external_admin_id, 777)
        self.assertEqual(su.role, User.Role.ADMIN)
        self.assertFalse(su.has_usable_password())

    def test_org_id_for_resolves_without_touching_last_used(self):
        key, raw = APIKey.generate('Partner B', self.admin, organization_id=self.org.id)
        self.assertEqual(APIKey.org_id_for(raw), self.org.id)
        key.refresh_from_db()
        self.assertIsNone(key.last_used_at)
        self.assertIsNone(APIKey.org_id_for('dcm_not-a-real-key'))

    def test_verify_throttles_last_used_writes(self):
        key, raw = APIKey.generate('Partner C', self.admin, organization_id=self.org.id)
        APIKey.verify(raw)
        key.refresh_from_db()
        first = key.last_used_at
        self.assertIsNotNone(first)
        APIKey.verify(raw)
        key.refresh_from_db()
        self.assertEqual(key.last_used_at, first)  # within the 60s window, no re-write

    def test_verify_rejects_expired(self):
        key, raw = APIKey.generate(
            'Partner D', self.admin, organization_id=self.org.id,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertIsNone(APIKey.verify(raw))


class APIKeyAuthTests(TestCase):
    def setUp(self):
        self.auth = APIKeyAuth()
        self.org = Organization.objects.create(
            name='Org One', slug='org-one', schema_name='org_one',
        )
        self.other_org = Organization.objects.create(
            name='Org Two', slug='org-two', schema_name='org_two',
        )
        self.admin = User.objects.create(
            email='admin@org-one.test', role=User.Role.ADMIN, organization=self.org,
        )

    def _key(self, **kw):
        kw.setdefault('organization_id', self.org.id)
        return APIKey.generate('K', self.admin, **kw)

    def test_read_key_authenticates_and_binds_service_user(self):
        key, raw = self._key()
        req = FakeRequest(method='GET', tenant=self.org)
        result = self.auth.authenticate(req, raw)
        self.assertEqual(result, key)
        self.assertEqual(req.user, key.service_user)
        self.assertEqual(req._jwt_payload, {'org_id': self.org.id})

    def test_rejects_when_request_tenant_is_a_different_org(self):
        _, raw = self._key()
        req = FakeRequest(method='GET', tenant=self.other_org)
        self.assertIsNone(self.auth.authenticate(req, raw))

    def test_rejects_when_no_tenant_resolved(self):
        _, raw = self._key()
        self.assertIsNone(self.auth.authenticate(FakeRequest(method='GET', tenant=None), raw))

    def test_read_only_key_cannot_mutate(self):
        _, raw = self._key(can_write=False)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            req = FakeRequest(method=method, tenant=self.org)
            self.assertIsNone(self.auth.authenticate(req, raw), method)

    def test_write_key_can_mutate(self):
        key, raw = self._key(can_write=True)
        req = FakeRequest(method='POST', tenant=self.org)
        self.assertEqual(self.auth.authenticate(req, raw), key)

    def test_rejects_when_service_user_disabled(self):
        key, raw = self._key()
        User.objects.filter(id=key.service_user_id).update(is_active=False)
        req = FakeRequest(method='GET', tenant=self.org)
        self.assertIsNone(self.auth.authenticate(req, raw))

    def test_rejects_garbage_key(self):
        self.assertIsNone(self.auth.authenticate(FakeRequest(tenant=self.org), 'dcm_nope'))
        self.assertIsNone(self.auth.authenticate(FakeRequest(tenant=self.org), 'not-even-prefixed'))


class MiddlewareTenantFromApiKeyTests(TestCase):
    """TenantResolverMiddleware calls _org_id_from_api_key to bind the request
    tenant from an X-API-Key before Ninja auth runs (the JWT path has no
    tenant to resolve from for a keyed request)."""

    def setUp(self):
        self.org = Organization.objects.create(
            name='MW Org', slug='mw-org', schema_name='mw_org',
        )
        self.admin = User.objects.create(
            email='admin@mw-org.test', role=User.Role.ADMIN, organization=self.org,
        )

    def test_resolves_org_id_from_x_api_key_header(self):
        _, raw = APIKey.generate('MW', self.admin, organization_id=self.org.id)
        self.assertEqual(_org_id_from_api_key(FakeRequest(api_key_header=raw)), self.org.id)

    def test_ignores_missing_or_non_dcm_header(self):
        self.assertIsNone(_org_id_from_api_key(FakeRequest()))
        self.assertIsNone(_org_id_from_api_key(FakeRequest(api_key_header='Bearer abc')))

    def test_inactive_key_resolves_no_org(self):
        key, raw = APIKey.generate('MW2', self.admin, organization_id=self.org.id)
        APIKey.objects.filter(id=key.id).update(is_active=False)
        self.assertIsNone(_org_id_from_api_key(FakeRequest(api_key_header=raw)))


class PartnerWhoamiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Who Org', slug='who-org', schema_name='who_org',
        )
        self.admin = User.objects.create(
            email='admin@who-org.test', role=User.Role.ADMIN, organization=self.org,
        )

    def test_whoami_reports_key_scope(self):
        from api.v1 import partner_whoami
        from apps.tenants.models import OrganizationTpmsAdminId

        OrganizationTpmsAdminId.objects.create(
            organization=self.org, admin_id=188, facility_name='North Site',
        )
        key, _ = APIKey.generate(
            'Partner', self.admin, organization_id=self.org.id,
            external_admin_id=188, can_write=False,
        )
        req = FakeRequest(method='GET', tenant=self.org)
        req.api_key = key
        req.user = key.service_user
        body = partner_whoami(req)
        self.assertEqual(body['auth_method'], 'api_key')
        self.assertEqual(body['organization_id'], self.org.pk)
        self.assertEqual(body['api_key_name'], 'Partner')
        self.assertFalse(body['can_write'])
        self.assertEqual(body['external_admin_id'], 188)
        self.assertEqual(body['tpms_facility_name'], 'North Site')


class SuperadminAPIKeyEndpointTests(TestCase):
    """A platform superadmin can mint / list / revoke a key for any org
    without an admin login there."""

    def setUp(self):
        from apps.tenants.models import OrganizationTpmsAdminId

        self.org = Organization.objects.create(
            name='Target Org', slug='target-org', schema_name='target_org',
        )
        # Two mapped TPMS practices for this org.
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=909, facility_name='Main Clinic')
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=910, facility_name='West Clinic')
        self.org_admin = User.objects.create(
            email='admin@target-org.test', role=User.Role.ADMIN,
            organization=self.org, external_admin_id=909,
        )
        self.superadmin = User.objects.create(
            email='root@platform.test', role=User.Role.ADMIN, is_superuser=True,
        )
        self.other = User.objects.create(email='nobody@x.test', role=User.Role.STAFF)

    def _req(self, user=None):
        return FakeRequest(method='POST', user=user or self.superadmin)

    def test_non_superadmin_is_rejected(self):
        with self.assertRaises(HttpError):
            create_superadmin_api_key(
                self._req(user=self.other),
                SuperadminAPIKeyCreate(organization_id=self.org.id, name='X'),
            )

    def test_inherits_practice_from_org_admin_when_mapped(self):
        status, payload = create_superadmin_api_key(
            self._req(),
            SuperadminAPIKeyCreate(organization_id=self.org.id, name='Partner X'),
        )
        self.assertEqual(status, 201)
        self.assertTrue(payload['raw_key'].startswith('dcm_'))
        self.assertEqual(payload['organization_id'], self.org.id)
        self.assertEqual(payload['external_admin_id'], 909)  # inherited, and it is mapped
        self.assertEqual(payload['tpms_facility_name'], 'Main Clinic')
        key = APIKey.objects.get(id=payload['id'])
        self.assertEqual(key.created_by_id, self.org_admin.id)
        self.assertEqual(key.service_user.organization_id, self.org.id)

    def test_explicit_mapped_practice_wins(self):
        _, payload = create_superadmin_api_key(
            self._req(),
            SuperadminAPIKeyCreate(
                organization_id=self.org.id, name='Scoped', external_admin_id=910,
            ),
        )
        self.assertEqual(payload['external_admin_id'], 910)
        self.assertEqual(payload['tpms_facility_name'], 'West Clinic')

    def test_unmapped_practice_id_is_rejected(self):
        with self.assertRaises(HttpError):
            create_superadmin_api_key(
                self._req(),
                SuperadminAPIKeyCreate(
                    organization_id=self.org.id, name='Bad', external_admin_id=555,
                ),
            )

    def test_unknown_org_is_404(self):
        with self.assertRaises(HttpError):
            create_superadmin_api_key(
                self._req(), SuperadminAPIKeyCreate(organization_id=999999, name='X'),
            )

    def test_list_filters_by_org_and_revoke_disables_service_user(self):
        _, payload = create_superadmin_api_key(
            self._req(),
            SuperadminAPIKeyCreate(organization_id=self.org.id, name='ToRevoke'),
        )
        rows = list_superadmin_api_keys(self._req(), organization_id=self.org.id)
        self.assertEqual([r['id'] for r in rows], [payload['id']])

        status, _ = revoke_superadmin_api_key(self._req(), payload['id'])
        self.assertEqual(status, 204)
        key = APIKey.objects.get(id=payload['id'])
        self.assertFalse(key.is_active)
        self.assertFalse(User.objects.get(id=key.service_user_id).is_active)
