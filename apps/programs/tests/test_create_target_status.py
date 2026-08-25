"""
POST /programs/settings/statuses — statuses are platform-managed (see
apps.tenants.models.DefaultTargetStatus, authored via Django Admin) rather
than something an org customizes. No org user can add a new one, regardless
of role or their settings_statuses_create permission — only a superuser.
"""
from django.db import connection
from django.test import Client as DjangoClient, TestCase
from django_tenants.utils import schema_context

from apps.accounts.auth import create_access_token
from apps.accounts.models import User
from apps.programs.models import TargetStatus
from apps.tenants.models import Domain, Organization
from shared.tenancy import tenant_context


class CreateTargetStatusTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-create-status', schema_name='test_org_create_status',
        )
        Domain.objects.create(domain='localhost', tenant=self.org, is_primary=True)

        self.admin = User.objects.create_user(
            email='admin@example.com', password='x',
            first_name='Admin', last_name='User', organization=self.org, role=User.Role.ADMIN,
        )
        self.superuser = User.objects.create_user(
            email='super@example.com', password='x',
            first_name='Super', last_name='User', organization=self.org, role=User.Role.ADMIN,
            is_superuser=True, is_staff=True,
        )

    def _create(self, user, key='new_status'):
        token = create_access_token(user, self.org.pk)
        try:
            return DjangoClient().post(
                '/api/v1/programs/settings/statuses',
                data={'key': key, 'label': 'New Status', 'color': '#111111', 'icon': 'circle'},
                content_type='application/json',
                HTTP_AUTHORIZATION=f'Bearer {token}',
                HTTP_HOST='localhost',
            )
        finally:
            connection.set_schema_to_public()

    def test_org_admin_cannot_create_a_status(self):
        response = self._create(self.admin)
        self.assertEqual(response.status_code, 403)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.assertFalse(TargetStatus.objects.filter(key='new_status').exists())

    def test_superuser_can_create_a_status(self):
        response = self._create(self.superuser)
        self.assertEqual(response.status_code, 201)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.assertTrue(TargetStatus.objects.filter(key='new_status').exists())
