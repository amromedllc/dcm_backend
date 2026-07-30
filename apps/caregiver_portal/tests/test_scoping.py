"""
apps.caregiver_portal: every handler must be hard-scoped to exactly the
caregiver's own linked client — these tests exist to catch a cross-client
data leak, which is the one failure mode that actually matters here.
"""
from unittest.mock import patch

from django.test import TestCase
from django_tenants.utils import schema_context
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.caregiver_portal.api import _scope, portal_notes, portal_note, portal_schedule
from apps.clients.models import Client
from apps.integrations.tpms_auth_client import TpmsAuthError
from apps.notes.models import LessonNote
from apps.tenants.models import Organization, OrganizationTpmsAdminId
from shared.tenancy import tenant_context


class FakeRequest:
    def __init__(self, user, tenant):
        self.user = user
        self.tenant = tenant


class CaregiverPortalScopingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Test Org Portal', slug='test-org-portal', schema_name='test_org_portal',
        )
        OrganizationTpmsAdminId.objects.create(organization=self.org, admin_id=501)

    def _caregiver(self, external_client_id, email='cg@example.com'):
        return User.objects.create(
            email=email, role=User.Role.CAREGIVER,
            external_client_id=external_client_id, organization=self.org,
        )

    def test_scope_resolves_the_linked_client(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = Client.objects.create(
                first_name='Kid', last_name='A', external_id='13086',
                external_admin_id=501, organization=self.org,
            )
            caregiver = self._caregiver(13086)
            external_id, resolved = _scope(FakeRequest(caregiver, self.org))
            self.assertEqual(external_id, 13086)
            self.assertEqual(resolved.id, client.id)

    def test_scope_403_when_client_not_synced_locally(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            caregiver = self._caregiver(99999)
            with self.assertRaises(HttpError) as ctx:
                _scope(FakeRequest(caregiver, self.org))
            self.assertEqual(ctx.exception.status_code, 403)

    def test_scope_403_when_client_practice_not_in_tenant(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                first_name='Kid', last_name='B', external_id='13087',
                external_admin_id=999,  # not in this org's tpms_admin_ids
                organization=self.org,
            )
            caregiver = self._caregiver(13087)
            with self.assertRaises(HttpError) as ctx:
                _scope(FakeRequest(caregiver, self.org))
            self.assertEqual(ctx.exception.status_code, 403)

    def test_notes_never_cross_client(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client_a = Client.objects.create(
                first_name='Kid', last_name='A', external_id='13086',
                external_admin_id=501, organization=self.org,
            )
            client_b = Client.objects.create(
                first_name='Kid', last_name='B', external_id='13087',
                external_admin_id=501, organization=self.org,
            )
            LessonNote.objects.create(
                external_client_id=13086, note_date='2026-01-01', body={},
                status=LessonNote.Status.APPROVED, requires_caregiver_signature=True,
            )
            note_b = LessonNote.objects.create(
                external_client_id=13087, note_date='2026-01-02', body={},
                status=LessonNote.Status.APPROVED, requires_caregiver_signature=True,
            )

            caregiver_a = self._caregiver(13086, email='cg-a@example.com')
            visible_to_a = portal_notes(FakeRequest(caregiver_a, self.org))
            self.assertEqual(len(visible_to_a), 1)
            self.assertNotEqual(visible_to_a[0]['id'], note_b.id)

            # caregiver A must not be able to fetch client B's note by id either
            with self.assertRaises(HttpError) as ctx:
                portal_note(FakeRequest(caregiver_a, self.org), note_b.id)
            self.assertEqual(ctx.exception.status_code, 404)

    def test_draft_and_rejected_notes_never_shown_to_caregiver(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                first_name='Kid', last_name='A', external_id='13086',
                external_admin_id=501, organization=self.org,
            )
            LessonNote.objects.create(
                external_client_id=13086, note_date='2026-01-01', body={},
                status=LessonNote.Status.DRAFT, requires_caregiver_signature=True,
            )
            LessonNote.objects.create(
                external_client_id=13086, note_date='2026-01-02', body={},
                status=LessonNote.Status.REJECTED, requires_caregiver_signature=True,
            )
            caregiver = self._caregiver(13086)
            self.assertEqual(portal_notes(FakeRequest(caregiver, self.org)), [])

    def test_notes_not_requiring_caregiver_signature_are_hidden(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                first_name='Kid', last_name='A', external_id='13086',
                external_admin_id=501, organization=self.org,
            )
            LessonNote.objects.create(
                external_client_id=13086, note_date='2026-01-01', body={},
                status=LessonNote.Status.APPROVED, requires_caregiver_signature=False,
            )
            caregiver = self._caregiver(13086)
            self.assertEqual(portal_notes(FakeRequest(caregiver, self.org)), [])

    def test_schedule_401_when_no_stored_tpms_token(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                first_name='Kid', last_name='A', external_id='13086',
                external_admin_id=501, organization=self.org,
            )
            caregiver = self._caregiver(13086)
            with patch('apps.caregiver_portal.api.get_tpms_access_token', return_value=None):
                with self.assertRaises(HttpError) as ctx:
                    portal_schedule(FakeRequest(caregiver, self.org))
                self.assertEqual(ctx.exception.status_code, 401)

    def test_schedule_401_and_clears_token_on_tpms_auth_failure(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                first_name='Kid', last_name='A', external_id='13086',
                external_admin_id=501, organization=self.org,
            )
            caregiver = self._caregiver(13086)
            with (
                patch('apps.caregiver_portal.api.get_tpms_access_token', return_value='tok'),
                patch('apps.caregiver_portal.api.clear_tpms_access_token') as mock_clear,
                patch(
                    'apps.caregiver_portal.api.list_client_portal_appointments',
                    side_effect=TpmsAuthError('expired', status_code=401),
                ),
            ):
                with self.assertRaises(HttpError) as ctx:
                    portal_schedule(FakeRequest(caregiver, self.org))
                self.assertEqual(ctx.exception.status_code, 401)
                mock_clear.assert_called_once_with(caregiver.id)
