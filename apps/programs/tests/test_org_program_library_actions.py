"""
Program Library row actions: Copy to client(s) (bulk assign), Duplicate,
and Lock/Unlock — the "…" menu added to /org-programs' Organization tab.
"""
import json

from django.db import connection
from django.test import Client as DjangoClient, TestCase
from django_tenants.utils import schema_context

from apps.accounts.auth import create_access_token
from apps.accounts.models import User
from apps.clients.models import Client as ClientModel
from apps.programs.models import Program, Target
from apps.tenants.models import Domain, Organization
from shared.tenancy import tenant_context


class OrgProgramLibraryActionsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org', slug='test-org-library-actions', schema_name='test_org_library_actions',
        )
        Domain.objects.create(domain='localhost', tenant=self.org, is_primary=True)
        self.admin = User.objects.create_user(
            email='admin@example.com', password='x',
            first_name='Admin', last_name='User', organization=self.org, role=User.Role.ADMIN,
        )
        self.token = create_access_token(self.admin, self.org.pk)

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.template = Program.objects.create(
                is_template=True, external_client_id=None,
                name='Mand Training', category=Program.Category.SKILL_ACQUISITION,
                created_by=self.admin,
            )
            Target.objects.create(
                program=self.template, name='Target 1',
                measurement_type='discrete_trial', status='active', created_by=self.admin,
            )
            self.client_a = ClientModel.objects.create(first_name='Ana', last_name='A')
            self.client_b = ClientModel.objects.create(first_name='Bo', last_name='B')

    def _post(self, path, data):
        try:
            return DjangoClient().post(
                path,
                data=json.dumps(data),
                content_type='application/json',
                HTTP_AUTHORIZATION=f'Bearer {self.token}',
                HTTP_HOST='localhost',
            )
        finally:
            connection.set_schema_to_public()

    def _patch(self, path, data):
        try:
            return DjangoClient().patch(
                path,
                data=json.dumps(data),
                content_type='application/json',
                HTTP_AUTHORIZATION=f'Bearer {self.token}',
                HTTP_HOST='localhost',
            )
        finally:
            connection.set_schema_to_public()

    def _delete(self, path):
        try:
            return DjangoClient().delete(
                path,
                HTTP_AUTHORIZATION=f'Bearer {self.token}',
                HTTP_HOST='localhost',
            )
        finally:
            connection.set_schema_to_public()

    # -- Copy to client(s) (bulk assign) ------------------------------------

    def test_assign_bulk_copies_program_to_each_client(self):
        response = self._post(
            f'/api/v1/org-programs/{self.template.id}/assign-bulk',
            {'client_ids': [self.client_a.id, self.client_b.id]},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(len(body), 2)

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            for client in (self.client_a, self.client_b):
                copy = Program.objects.get(external_client_id=client.id, is_template=False)
                self.assertEqual(copy.name, 'Mand Training')
                target = copy.targets.get()
                self.assertEqual(target.status, 'waiting')  # reset, not copied verbatim

    def test_assign_bulk_rejects_inaccessible_client(self):
        response = self._post(
            f'/api/v1/org-programs/{self.template.id}/assign-bulk',
            {'client_ids': [999999]},
        )
        self.assertEqual(response.status_code, 404)

    # -- Duplicate / Save as template -----------------------------------------

    def test_duplicate_creates_new_template_with_default_name(self):
        response = self._post(f'/api/v1/org-programs/{self.template.id}/duplicate', {})
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['name'], 'Mand Training (Copy)')
        self.assertTrue(body['is_template'])

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            copy = Program.objects.get(id=body['id'])
            self.assertTrue(copy.is_template)
            self.assertIsNone(copy.external_client_id)
            self.assertEqual(copy.targets.count(), 1)

    def test_duplicate_accepts_custom_name(self):
        response = self._post(f'/api/v1/org-programs/{self.template.id}/duplicate', {'name': 'Mand Training v2'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['name'], 'Mand Training v2')

    def test_duplicate_allowed_even_when_source_is_locked(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.template.is_locked = True
            self.template.save(update_fields=['is_locked'])
        response = self._post(f'/api/v1/org-programs/{self.template.id}/duplicate', {})
        self.assertEqual(response.status_code, 201)

    # -- Lock / Unlock --------------------------------------------------------

    def test_lock_then_edit_is_rejected(self):
        lock_response = self._post(f'/api/v1/org-programs/{self.template.id}/lock', {'locked': True})
        self.assertEqual(lock_response.status_code, 200)
        self.assertTrue(lock_response.json()['is_locked'])

        edit_response = self._patch(f'/api/v1/org-programs/{self.template.id}', {'name': 'Renamed'})
        self.assertEqual(edit_response.status_code, 400)

        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.template.refresh_from_db()
            self.assertEqual(self.template.name, 'Mand Training')  # unchanged

    def test_lock_then_delete_is_rejected(self):
        self._post(f'/api/v1/org-programs/{self.template.id}/lock', {'locked': True})
        delete_response = self._delete(f'/api/v1/org-programs/{self.template.id}')
        self.assertEqual(delete_response.status_code, 400)

    def test_unlock_allows_edit_again(self):
        self._post(f'/api/v1/org-programs/{self.template.id}/lock', {'locked': True})
        self._post(f'/api/v1/org-programs/{self.template.id}/lock', {'locked': False})
        edit_response = self._patch(f'/api/v1/org-programs/{self.template.id}', {'name': 'Renamed'})
        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(edit_response.json()['name'], 'Renamed')

    def test_org_program_schema_serializes_is_locked(self):
        response = self._post(f'/api/v1/org-programs/{self.template.id}/lock', {'locked': True})
        self.assertEqual(response.status_code, 200)
        get_response = DjangoClient().get(
            f'/api/v1/org-programs/{self.template.id}',
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
            HTTP_HOST='localhost',
        )
        connection.set_schema_to_public()
        self.assertEqual(get_response.status_code, 200)
        self.assertTrue(get_response.json()['is_locked'])
