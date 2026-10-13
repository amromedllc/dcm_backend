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
from django.test import TestCase

from apps.accounts.auth import create_access_token, create_refresh_token, decode_token
from apps.accounts.models import User
from apps.tenants.models import Domain, Organization


class TokenRefreshRouteTests(TestCase):
    def setUp(self):
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
