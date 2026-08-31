"""
resolve_template_tokens turns a 'forms' template's [data-dynamic-field]
tokens into concrete strings for one note.
"""
from datetime import date

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.clients.models import Client
from apps.notes.models import LessonNote, NoteTemplate
from apps.notes.services import resolve_template_tokens
from apps.sessions.models import SessionRun
from apps.tenants.models import Organization
from shared.tenancy import tenant_context

_BODY = (
    '<p>Seen by <span data-dynamic-field="true" data-key="client.full_name" '
    'data-label="Client Full Name">[Client Full Name]</span> on '
    '<span data-dynamic-field="true" data-key="session.date" data-label="Session Date">[Session Date]</span></p>'
)


class TokenResolverTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='TK Org', slug='tk-org', schema_name='tk_org',
        )
        self.staff = User.objects.create_user(
            email='tk-staff@example.com', password='x', first_name='Dana', last_name='Ray',
            organization=self.org, role=User.Role.STAFF,
        )
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.client_row = Client.objects.create(
                first_name='Sam', last_name='Lee', date_of_birth=date(2018, 4, 2),
                organization=self.org,
            )
            self.forms_tmpl = NoteTemplate.objects.create(
                name='Visit form', template_type='forms', body_template=_BODY, fields=[],
            )
            self.notes_tmpl = NoteTemplate.objects.create(
                name='Plain', template_type='notes', fields=[],
            )
            self.session = SessionRun.objects.create(external_client_id=self.client_row.id, staff=self.staff)

    def _note(self, template, **kw):
        return LessonNote.objects.create(
            external_client_id=self.client_row.id, staff=self.staff, template=template,
            note_date=date(2026, 5, 1), **kw,
        )

    def test_resolves_client_staff_and_session_tokens(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            tokens = resolve_template_tokens(self._note(self.forms_tmpl, session_run=self.session))
            self.assertEqual(tokens['client.full_name'], 'Sam Lee')
            self.assertEqual(tokens['client.first_name'], 'Sam')
            self.assertEqual(tokens['client.dob'], '2018-04-02')
            self.assertEqual(tokens['user.full_name'], 'Dana Ray')
            self.assertEqual(tokens['staff.email'], 'tk-staff@example.com')
            self.assertIn('session.date', tokens)

    def test_empty_for_non_forms_template(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            self.assertEqual(resolve_template_tokens(self._note(self.notes_tmpl)), {})

    def test_session_date_falls_back_to_note_date_without_session(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            tokens = resolve_template_tokens(self._note(self.forms_tmpl))
            self.assertEqual(tokens['session.date'], '2026-05-01')
            self.assertNotIn('session.start_time', tokens)
