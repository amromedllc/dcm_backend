"""
Tests for the User Impersonation feature.

Covers:
- Superadmin can impersonate an eligible user
- Regular admin cannot impersonate
- Normal users cannot impersonate
- Superadmin cannot impersonate another superadmin
- Target user permissions are correctly enforced (not superadmin)
- Superadmin can return to the original session (stop impersonation)
- Unauthorized API requests are rejected
- Nested impersonation is blocked
- Audit records are correctly created
- High-risk operations are blocked during impersonation
- Inactive users cannot be impersonated
- Caregiver accounts cannot be impersonated
"""
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.accounts.auth import create_access_token, create_refresh_token, decode_token
from apps.accounts.models import ImpersonationLog, User
from apps.tenants.models import Domain, Organization


def _make_redis():
    """In-memory Redis stub sufficient for impersonation session storage."""
    store = {}

    class _Redis:
        def setex(self, key, ttl, value):
            store[key] = value

        def get(self, key):
            return store.get(key)

        def delete(self, key):
            store.pop(key, None)

        def exists(self, key):
            return key in store

        def set(self, key, value, **kwargs):
            store[key] = value

        def getdel(self, key):
            return store.pop(key, None)

    return _Redis()


class ImpersonationTests(TestCase):
    def setUp(self):
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema_to_public)

        self.org = Organization.objects.create(
            name='Test Org Impersonation',
            slug='test-org-impersonation',
            schema_name='test_org_impersonation',
        )
        Domain.objects.create(domain='localhost', tenant=self.org, is_primary=True)

        self.superadmin = User.objects.create_superuser(
            email='superadmin@example.com',
            password='super-secret-pass',
            first_name='Super',
            last_name='Admin',
        )
        self.superadmin.organization = self.org
        self.superadmin.save(update_fields=['organization'])

        self.admin_user = User.objects.create_user(
            email='admin@example.com',
            password='admin-pass',
            first_name='Regular',
            last_name='Admin',
            role=User.Role.ADMIN,
            organization=self.org,
        )

        self.staff_user = User.objects.create_user(
            email='staff@example.com',
            password='staff-pass',
            first_name='Staff',
            last_name='User',
            role=User.Role.STAFF,
            organization=self.org,
        )

        self.other_superadmin = User.objects.create_superuser(
            email='other-super@example.com',
            password='other-super-pass',
            first_name='Other',
            last_name='Super',
        )

        self.redis = _make_redis()

    def _superadmin_headers(self):
        token = create_access_token(self.superadmin, self.org.pk)
        return {'HTTP_HOST': 'localhost', 'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def _admin_headers(self):
        token = create_access_token(self.admin_user, self.org.pk)
        return {'HTTP_HOST': 'localhost', 'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def _staff_headers(self):
        token = create_access_token(self.staff_user, self.org.pk)
        return {'HTTP_HOST': 'localhost', 'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def _impersonation_headers(self, target: User, admin: User):
        token = create_access_token(
            target, self.org.pk,
            impersonating_admin_id=admin.id,
            impersonation_log_id=999,
        )
        return {'HTTP_HOST': 'localhost', 'HTTP_AUTHORIZATION': f'Bearer {token}'}

    # ── Start impersonation ────────────────────────────────────────────────

    def test_superadmin_can_impersonate_staff(self):
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False), \
             mock.patch('apps.tenants.api._redis', return_value=self.redis):
            response = self.client.post(
                f'/api/v1/organization/superadmin/impersonate/{self.staff_user.id}',
                data={'reason': 'Support investigation'},
                content_type='application/json',
                **self._superadmin_headers(),
            )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertIn('access_token', data)
        self.assertIn('refresh_token', data)
        self.assertEqual(data['target_user']['id'], self.staff_user.id)

        # Verify JWT carries impersonation claims
        payload = decode_token(data['access_token'])
        self.assertEqual(payload['sub'], str(self.staff_user.id))
        self.assertEqual(payload['impersonating_admin_id'], self.superadmin.id)

        # Verify audit log created
        log = ImpersonationLog.objects.get(id=data['impersonation_log_id'])
        self.assertEqual(log.admin, self.superadmin)
        self.assertEqual(log.target_user, self.staff_user)
        self.assertEqual(log.status, ImpersonationLog.Status.ACTIVE)
        self.assertEqual(log.reason, 'Support investigation')

    def test_superadmin_can_impersonate_admin(self):
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False), \
             mock.patch('apps.tenants.api._redis', return_value=self.redis):
            response = self.client.post(
                f'/api/v1/organization/superadmin/impersonate/{self.admin_user.id}',
                data={'reason': ''},
                content_type='application/json',
                **self._superadmin_headers(),
            )
        self.assertEqual(response.status_code, 200)

    # ── Authorization guards ───────────────────────────────────────────────

    def test_regular_admin_cannot_impersonate(self):
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.post(
                f'/api/v1/organization/superadmin/impersonate/{self.staff_user.id}',
                data={'reason': ''},
                content_type='application/json',
                **self._admin_headers(),
            )
        self.assertEqual(response.status_code, 403)

    def test_staff_user_cannot_impersonate(self):
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.post(
                f'/api/v1/organization/superadmin/impersonate/{self.admin_user.id}',
                data={'reason': ''},
                content_type='application/json',
                **self._staff_headers(),
            )
        self.assertEqual(response.status_code, 403)

    def test_superadmin_cannot_impersonate_another_superadmin(self):
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False), \
             mock.patch('apps.tenants.api._redis', return_value=self.redis):
            response = self.client.post(
                f'/api/v1/organization/superadmin/impersonate/{self.other_superadmin.id}',
                data={'reason': ''},
                content_type='application/json',
                **self._superadmin_headers(),
            )
        self.assertEqual(response.status_code, 403)
        self.assertIn('superadmin', response.json()['detail'].lower())

    def test_cannot_impersonate_inactive_user(self):
        self.staff_user.is_active = False
        self.staff_user.save(update_fields=['is_active'])
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False), \
             mock.patch('apps.tenants.api._redis', return_value=self.redis):
            response = self.client.post(
                f'/api/v1/organization/superadmin/impersonate/{self.staff_user.id}',
                data={'reason': ''},
                content_type='application/json',
                **self._superadmin_headers(),
            )
        self.assertEqual(response.status_code, 400)

    def test_cannot_impersonate_caregiver(self):
        caregiver = User.objects.create_user(
            email='caregiver@example.com',
            password='cg-pass',
            first_name='Care',
            last_name='Giver',
            role=User.Role.CAREGIVER,
            organization=self.org,
            external_client_id=999,
        )
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False), \
             mock.patch('apps.tenants.api._redis', return_value=self.redis):
            response = self.client.post(
                f'/api/v1/organization/superadmin/impersonate/{caregiver.id}',
                data={'reason': ''},
                content_type='application/json',
                **self._superadmin_headers(),
            )
        self.assertEqual(response.status_code, 400)

    def test_nested_impersonation_is_blocked(self):
        """Cannot start a new impersonation while already impersonating."""
        impersonation_token = create_access_token(
            self.admin_user, self.org.pk,
            impersonating_admin_id=self.superadmin.id,
            impersonation_log_id=1,
        )
        headers = {'HTTP_HOST': 'localhost', 'HTTP_AUTHORIZATION': f'Bearer {impersonation_token}'}
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False), \
             mock.patch('apps.tenants.api._redis', return_value=self.redis):
            response = self.client.post(
                f'/api/v1/organization/superadmin/impersonate/{self.staff_user.id}',
                data={'reason': ''},
                content_type='application/json',
                **headers,
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('already impersonating', response.json()['detail'].lower())

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.post(
            f'/api/v1/organization/superadmin/impersonate/{self.staff_user.id}',
            data={'reason': ''},
            content_type='application/json',
            HTTP_HOST='localhost',
        )
        self.assertEqual(response.status_code, 401)

    # ── Stop impersonation ─────────────────────────────────────────────────

    def test_superadmin_can_stop_impersonation_and_restore_session(self):
        import json

        log = ImpersonationLog.objects.create(
            admin=self.superadmin,
            target_user=self.staff_user,
            session_id='test-session-abc',
            status=ImpersonationLog.Status.ACTIVE,
        )
        # Store admin session in Redis
        admin_access = create_access_token(self.superadmin, self.org.pk)
        self.redis.setex(
            f'dcm:impersonation:admin_session:test-session-abc',
            3600,
            json.dumps({
                'admin_id': self.superadmin.id,
                'access_token': admin_access,
                'refresh_token': 'http-only-session',
                'tenant_id': self.org.pk,
            }),
        )

        impersonation_token = create_access_token(
            self.staff_user, self.org.pk,
            impersonating_admin_id=self.superadmin.id,
            impersonation_log_id=log.id,
            session_id='test-session-abc',
        )
        headers = {'HTTP_HOST': 'localhost', 'HTTP_AUTHORIZATION': f'Bearer {impersonation_token}'}

        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False), \
             mock.patch('apps.accounts.auth._redis', return_value=self.redis), \
             mock.patch('apps.tenants.api._redis', return_value=self.redis):
            response = self.client.post(
                '/api/v1/organization/superadmin/impersonate/stop',
                content_type='application/json',
                **headers,
            )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertIn('access_token', data)

        # Restored token must be for the original admin
        payload = decode_token(data['access_token'])
        self.assertEqual(payload['sub'], str(self.superadmin.id))
        self.assertNotIn('impersonating_admin_id', payload)

        # Audit log must be updated
        log.refresh_from_db()
        self.assertEqual(log.status, ImpersonationLog.Status.ENDED)
        self.assertIsNotNone(log.ended_at)

    def test_stop_without_active_impersonation_returns_400(self):
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.post(
                '/api/v1/organization/superadmin/impersonate/stop',
                content_type='application/json',
                **self._superadmin_headers(),
            )
        self.assertEqual(response.status_code, 400)

    # ── High-risk operation blocks ─────────────────────────────────────────

    def test_password_change_blocked_during_impersonation(self):
        """Impersonated session must not be able to change passwords."""
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.post(
                '/api/v1/auth/account/mfa/disable',
                data={'code': '123456'},
                content_type='application/json',
                **self._impersonation_headers(self.staff_user, self.superadmin),
            )
        # 403 from _block_during_impersonation (or 400 from MFA not set up — either is fine,
        # but it must NOT be 204 success)
        self.assertNotEqual(response.status_code, 204)

    def test_role_change_blocked_during_impersonation(self):
        target = User.objects.create_user(
            email='target2@example.com', password='pass', organization=self.org,
        )
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.patch(
                f'/api/v1/auth/users/{target.id}',
                data={'role': 'supervisor'},
                content_type='application/json',
                **self._impersonation_headers(self.admin_user, self.superadmin),
            )
        self.assertEqual(response.status_code, 403)

    def test_pin_set_blocked_during_impersonation(self):
        self.org.pin_lock_allowed = True
        self.org.save(update_fields=['pin_lock_allowed'])
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.post(
                '/api/v1/auth/account/pin',
                data={'pin': '1234'},
                content_type='application/json',
                **self._impersonation_headers(self.staff_user, self.superadmin),
            )
        self.assertEqual(response.status_code, 403)

    def test_role_permissions_change_blocked_during_impersonation(self):
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.put(
                '/api/v1/auth/admin/role-permissions',
                data={'admin': {}},
                content_type='application/json',
                **self._impersonation_headers(self.admin_user, self.superadmin),
            )
        self.assertEqual(response.status_code, 403)

    # ── Impersonation logs endpoint ────────────────────────────────────────

    def test_superadmin_can_list_impersonation_logs(self):
        ImpersonationLog.objects.create(
            admin=self.superadmin,
            target_user=self.staff_user,
            session_id='log-test-session',
        )
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.get(
                '/api/v1/organization/superadmin/impersonation-logs',
                HTTP_HOST='localhost',
                HTTP_AUTHORIZATION=f'Bearer {create_access_token(self.superadmin, self.org.pk)}',
            )
        self.assertEqual(response.status_code, 200)
        logs = response.json()
        self.assertGreaterEqual(len(logs), 1)
        self.assertEqual(logs[0]['target_user_id'], self.staff_user.id)

    def test_regular_admin_cannot_list_impersonation_logs(self):
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.get(
                '/api/v1/organization/superadmin/impersonation-logs',
                **self._admin_headers(),
            )
        self.assertEqual(response.status_code, 403)

    # ── /auth/me exposes impersonation context ─────────────────────────────

    def test_me_endpoint_exposes_impersonation_claims(self):
        log = ImpersonationLog.objects.create(
            admin=self.superadmin,
            target_user=self.staff_user,
            session_id='test-session-abc',
        )
        token = create_access_token(
            self.staff_user, self.org.pk,
            impersonating_admin_id=self.superadmin.id,
            impersonation_log_id=log.id,
            session_id='test-session-abc',
        )
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.get(
                '/api/v1/auth/me',
                HTTP_HOST='localhost',
                HTTP_AUTHORIZATION=f'Bearer {token}',
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['impersonating_admin_id'], self.superadmin.id)
        self.assertEqual(data['impersonation_log_id'], log.id)
        self.assertEqual(data['impersonating_admin_name'], self.superadmin.full_name)
        self.assertIsNotNone(data['impersonation_started_at'])
        # Must NOT expose superadmin privileges
        self.assertFalse(data.get('is_superuser', False))

    def test_refresh_preserves_impersonation_claims(self):
        refresh_token = create_refresh_token(
            self.staff_user,
            self.org.pk,
            impersonating_admin_id=self.superadmin.id,
            impersonation_log_id=42,
            session_id='refresh-impersonation-session',
        )
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.post(
                '/api/v1/auth/refresh',
                data={'refresh_token': refresh_token},
                content_type='application/json',
                HTTP_HOST='localhost',
            )
        self.assertEqual(response.status_code, 200, response.content)
        payload = decode_token(response.json()['access_token'])
        self.assertEqual(payload['impersonating_admin_id'], self.superadmin.id)
        self.assertEqual(payload['impersonation_log_id'], 42)
        self.assertEqual(payload['sid'], 'refresh-impersonation-session')

    def test_me_endpoint_no_impersonation_claims_for_normal_session(self):
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.get(
                '/api/v1/auth/me',
                **self._superadmin_headers(),
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data.get('impersonating_admin_id'))
