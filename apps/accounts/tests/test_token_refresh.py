"""
Regression test for /auth/refresh — this endpoint's @router.post('/refresh', ...)
decorator was accidentally dropped in a July 2026 "security fixes" commit
when logout/logout_all were added above it in api.py, leaving refresh_token
as a plain, unregistered function. No test hit the endpoint through the
real URL (Django test client), only the view function directly, so a route
that silently stopped resolving at all went unnoticed for months — every
session in the app was one token expiry away from an unexplained logout.

These tests go through self.client + HTTP_HOST, not the Python function
directly, specifically so a missing/misplaced @router decorator fails here
again if it ever regresses.
"""
from unittest import mock
from django.db import connection
from django.test import TestCase

from apps.accounts.auth import create_access_token, create_refresh_token, decode_token
from apps.accounts.models import User
from apps.tenants.models import Domain, Organization


class TokenRefreshRouteTests(TestCase):
    def setUp(self):
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema_to_public)
        self.org = Organization.objects.create(
            name='Test Org Refresh', slug='test-org-refresh', schema_name='test_org_refresh',
        )
        Domain.objects.create(domain='localhost', tenant=self.org, is_primary=True)
        self.user = User.objects.create_user(
            email='refresh@example.com', password='secret-pass',
            first_name='Refresh', last_name='User', organization=self.org,
        )

    def test_refresh_endpoint_is_routed_and_issues_a_new_access_token(self):
        refresh_token = create_refresh_token(self.user, self.org.pk)
        response = self.client.post(
            '/api/v1/auth/refresh',
            data={'refresh_token': refresh_token},
            content_type='application/json',
            HTTP_HOST='localhost',
        )
        self.assertEqual(response.status_code, 200)
        new_access_token = response.json()['access_token']
        payload = decode_token(new_access_token)
        self.assertEqual(payload['sub'], str(self.user.id))
        self.assertEqual(payload['type'], 'access')

    def test_refresh_rejects_an_access_token(self):
        """A 404 here (route unregistered) is exactly the regression this
        file guards against — it must be 401, not 404."""
        access_token = create_access_token(self.user, self.org.pk)
        response = self.client.post(
            '/api/v1/auth/refresh',
            data={'refresh_token': access_token},
            content_type='application/json',
            HTTP_HOST='localhost',
        )
        self.assertEqual(response.status_code, 401)

    def test_refresh_rejects_garbage_token(self):
        response = self.client.post(
            '/api/v1/auth/refresh',
            data={'refresh_token': 'not-a-real-token'},
            content_type='application/json',
            HTTP_HOST='localhost',
        )
        self.assertEqual(response.status_code, 401)

    def test_idle_lock_is_enforced_on_me_refresh_and_activity_routes(self):
        self.org.automatic_logout_enabled = True
        self.org.save(update_fields=['automatic_logout_enabled'])
        access = create_access_token(self.user, self.org.pk)
        refresh = create_refresh_token(self.user, self.org.pk)
        redis = mock.Mock()
        redis.exists.return_value = False
        redis.get.return_value = None
        redis.eval.return_value = 0
        with mock.patch('apps.accounts.auth._redis', return_value=redis):
            headers = dict(HTTP_HOST='localhost', HTTP_AUTHORIZATION=f'Bearer {access}')
            self.assertEqual(self.client.get('/api/v1/auth/me', **headers).status_code, 423)
            self.assertEqual(self.client.post('/api/v1/auth/session/activity', **headers).status_code, 423)
            self.assertEqual(self.client.post('/api/v1/auth/refresh',
                data={'refresh_token': refresh}, content_type='application/json',
                HTTP_HOST='localhost').status_code, 423)
            redis.eval.return_value = 1
            self.assertEqual(self.client.post('/api/v1/auth/session/activity', **headers).status_code, 204)

    def test_pin_change_and_password_recovery_require_proof_over_http(self):
        self.org.pin_lock_allowed = True
        self.org.save(update_fields=['pin_lock_allowed'])
        self.user.set_pin('1234')
        self.user.save()
        headers = dict(HTTP_HOST='localhost', HTTP_AUTHORIZATION=f'Bearer {create_access_token(self.user, self.org.pk)}')
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.post('/api/v1/auth/account/pin', data={'pin': '5678'},
                content_type='application/json', **headers)
            self.assertEqual(response.status_code, 403)
            response = self.client.post('/api/v1/auth/account/pin',
                data={'pin': '5678', 'password': 'secret-pass'}, content_type='application/json', **headers)
            self.assertEqual(response.status_code, 200)
            self.user.refresh_from_db()
            self.assertTrue(self.user.check_pin('5678'))
            for attempt in range(5):
                response = self.client.post('/api/v1/auth/pin/verify', data={'pin': '9999'},
                    content_type='application/json', **headers)
                self.assertEqual(response.status_code, 429 if attempt == 4 else 400)
            from apps.audit.models import AuditLog
            events = AuditLog.all_organizations.filter(organization=self.org, model='AccountSecurity')
            self.assertEqual(events.filter(object_repr='pin.lockout').count(), 1)
            self.assertEqual(events.filter(object_repr='pin.verify', changes__outcome='rejected').count(), 5)
            self.assertEqual(events.filter(object_repr='pin.set', changes__outcome='success').count(), 1)
            self.assertEqual(events.filter(object_repr='pin.set', changes__outcome='rejected').count(), 1)
            for entry in events:
                serialized = str(entry.changes)
                self.assertNotIn('secret-pass', serialized)
                self.assertNotIn('5678', serialized)
                self.assertNotIn('9999', serialized)


    def test_pin_disable_and_delete_require_verification_over_http(self):
        self.org.pin_lock_allowed = True
        self.org.save(update_fields=['pin_lock_allowed'])
        self.user.set_pin('1234')
        self.user.save()
        headers = dict(HTTP_HOST='localhost', HTTP_AUTHORIZATION=f'Bearer {create_access_token(self.user, self.org.pk)}')
        with mock.patch('apps.accounts.auth.is_token_blocked', return_value=False):
            response = self.client.post('/api/v1/auth/account/pin/toggle', data={'enabled': False},
                content_type='application/json', **headers)
            self.assertEqual(response.status_code, 403)
            response = self.client.delete('/api/v1/auth/account/pin', **headers)
            self.assertEqual(response.status_code, 403)
            response = self.client.post('/api/v1/auth/account/pin/toggle',
                data={'enabled': False, 'current_pin': '1234'}, content_type='application/json', **headers)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['pin_lock_enabled'])
            self.assertTrue(response.json()['has_pin'])
            response = self.client.delete('/api/v1/auth/account/pin', data={'password': 'wrong'},
                content_type='application/json', **headers)
            self.assertEqual(response.status_code, 400)
            self.user.refresh_from_db()
            self.assertTrue(self.user.has_pin)
            self.assertEqual(self.user.pin_failed_attempts, 1)
            response = self.client.delete('/api/v1/auth/account/pin', data={'password': 'secret-pass'},
                content_type='application/json', **headers)
            self.assertEqual(response.status_code, 204)
            self.user.refresh_from_db()
            self.assertFalse(self.user.has_pin)
