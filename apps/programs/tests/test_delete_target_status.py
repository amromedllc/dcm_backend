"""
DELETE /programs/settings/statuses/{pk} — built-in org-default statuses
(created_by=None, copied at org-creation time from platform
DefaultTargetStatus templates — see apps.tenants.services) can only be
deleted by a platform superuser, since every practice in the org depends on
those keys (existing targets, workflow criteria). A practice's own
custom-created status can still be deleted by anyone with the ordinary
settings_statuses_delete permission.
"""
from django.db import connection
from django.test import Client as DjangoClient, TestCase
from django_tenants.utils import schema_context

from apps.accounts.auth import create_access_token
from apps.accounts.models import User
from apps.programs.models import TargetStatus
from apps.tenants.models import Domain, Organization
from shared.tenancy import tenant_context


class DeleteTargetStatusTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-delete-status', schema_name='test_org_delete_status',
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

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.default_status = TargetStatus.objects.create(
                key='waiting', label='Waiting', created_by=None,
            )
            self.custom_status = TargetStatus.objects.create(
                key='custom', label='Custom', created_by=self.admin,
            )

    def _delete(self, user, status_id):
        token = create_access_token(user, self.org.pk)
        try:
            return DjangoClient().delete(
                f'/api/v1/programs/settings/statuses/{status_id}',
                HTTP_AUTHORIZATION=f'Bearer {token}',
                HTTP_HOST='localhost',
            )
        finally:
            connection.set_schema_to_public()

    def test_org_admin_cannot_delete_a_default_status(self):
        response = self._delete(self.admin, self.default_status.id)
        self.assertEqual(response.status_code, 403)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.assertTrue(TargetStatus.objects.filter(id=self.default_status.id).exists())

    def test_superuser_can_delete_a_default_status(self):
        response = self._delete(self.superuser, self.default_status.id)
        self.assertEqual(response.status_code, 204)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.assertFalse(TargetStatus.objects.filter(id=self.default_status.id).exists())

    def test_org_admin_can_still_delete_their_own_custom_status(self):
        response = self._delete(self.admin, self.custom_status.id)
        self.assertEqual(response.status_code, 204)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.assertFalse(TargetStatus.objects.filter(id=self.custom_status.id).exists())
