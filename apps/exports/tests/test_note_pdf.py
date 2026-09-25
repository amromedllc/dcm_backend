"""The session-note PDF renders the template body with real values, not a bare field list."""
from datetime import date

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.clients.models import Client
from apps.exports.note_pdf import body_blocks, render_note_pdf
from apps.notes.models import LessonNote, NoteSignature, NoteTemplate
from apps.tenants.models import Organization
from shared.tenancy import tenant_context

BODY = (
    '<h2>Session summary</h2>'
    '<p>Client <span data-dynamic-field="true" data-key="client.full_name" data-label="Client">[Client]</span> '
    'responded <span data-custom-field="true" data-key="resp" data-label="Response">[Response]</span>.</p>'
    '<ul><li>First</li><li>Second</li></ul>'
    '<table><tr><td>A &lt; B</td><td><b>bold</b></td></tr></table>'
)


class NotePdfTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='PDF Org', slug='pdf-org', schema_name='pdf_org')
        self.staff = User.objects.create_user(
            email='pdf@example.com', password='x', first_name='Dana', last_name='Ray',
            organization=self.org, role=User.Role.STAFF,
        )

    def test_body_blocks_substitute_values_and_tokens(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = NoteTemplate.objects.create(
                name='T', body_template=BODY, fields=[{'key': 'resp', 'label': 'Response', 'type': 'text'}],
            )
            blocks = body_blocks(template, {'resp': 'well & calmly'}, {'client.full_name': 'Sam Lee'})
        text = ' '.join(str(b) for b in blocks)
        self.assertIn('Sam Lee', text)
        self.assertIn('well &amp; calmly', text)
        self.assertNotIn('[Response]', text)
        self.assertEqual(blocks[0][2], 'h2')
        self.assertEqual(blocks[-1][0], 'table')

    def test_renders_a_valid_pdf_with_signature(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            client = Client.objects.create(first_name='Sam', last_name='Lee', date_of_birth=date(2018, 4, 2), organization=self.org)
            template = NoteTemplate.objects.create(
                name='Session note', body_template=BODY, fields=[{'key': 'resp', 'label': 'Response', 'type': 'text'}],
            )
            note = LessonNote.objects.create(
                external_client_id=client.id, staff=self.staff, template=template,
                note_date=date(2026, 5, 1), body={'resp': 'well'},
            )
            NoteSignature.objects.create(
                note=note, organization=self.org, signer_id=self.staff.id, signer_name='Dana Ray',
                signer_role='BCBA', signature_type='staff',
            )
            note = LessonNote.objects.select_related('staff', 'template', 'organization').prefetch_related('signatures').get(id=note.id)
            pdf = render_note_pdf(note)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 2000)

    def test_field_list_fallback_and_free_text(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            template = NoteTemplate.objects.create(name='Plain', fields=[{'key': 'a', 'label': 'Summary', 'type': 'textarea'}])
            n1 = LessonNote.objects.create(external_client_id=1, staff=self.staff, template=template, note_date=date(2026, 5, 1), body={'a': '5 < 10'})
            n2 = LessonNote.objects.create(external_client_id=1, staff=self.staff, note_date=date(2026, 5, 1), body={'text': '<div>Hi <b>there</b></div>'})
            self.assertTrue(render_note_pdf(n1).startswith(b'%PDF'))
            self.assertTrue(render_note_pdf(n2).startswith(b'%PDF'))
