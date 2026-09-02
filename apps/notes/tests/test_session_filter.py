"""
GET /notes?session_run_id= — filters notes down to the ones linked to one
session run, powering the session panel's "view the note filled for this
session" link-back.
"""
from datetime import date

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.notes.models import LessonNote, NoteTemplate
from apps.sessions.models import SessionRun
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


class SessionNoteFilterTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='SF Org', slug='sf-org', schema_name='sf_org',
        )
        self.staff = User.objects.create_user(
            email='sf-staff@example.com', password='x', first_name='S', last_name='F',
            organization=self.org, role=User.Role.STAFF,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.tmpl = NoteTemplate.objects.create(name='Plain', fields=[])
            self.session_a = SessionRun.objects.create(external_client_id=1, staff=self.staff)
            self.session_b = SessionRun.objects.create(external_client_id=1, staff=self.staff)
            self.note_a = LessonNote.objects.create(
                external_client_id=1, staff=self.staff, template=self.tmpl,
                session_run=self.session_a, note_date=date(2026, 5, 1),
            )
            self.note_unlinked = LessonNote.objects.create(
                external_client_id=1, staff=self.staff, template=self.tmpl,
                note_date=date(2026, 5, 1),
            )

    def test_filters_to_the_linked_session_only(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            from apps.notes.api import list_notes

            class FakeRequest:
                user = self.staff

            result = list_notes(FakeRequest(), session_run_id=self.session_a.id)
            self.assertEqual([r['id'] for r in result], [self.note_a.id])

    def test_no_match_for_a_different_session(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            from apps.notes.api import list_notes

            class FakeRequest:
                user = self.staff

            result = list_notes(FakeRequest(), session_run_id=self.session_b.id)
            self.assertEqual(result, [])
