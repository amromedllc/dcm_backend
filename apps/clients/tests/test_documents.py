"""Private client files: validation, row-level access, storage and retention."""
import io
import json
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from ninja.errors import HttpError

from apps.accounts.models import RolePermission, User
from apps.audit.models import AuditLog
from apps.clients import documents_api as api
from apps.clients.document_schemas import DocumentUpdate, FolderInput
from apps.clients.document_storage import PrivateDocumentStorage
from apps.clients.document_validation import MAX_DOCUMENT_BYTES, validate_document
from apps.clients.models import Client, ClientDocument, ClientDocumentFolder, ClientStaffAssignment
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


def text_file(content=b'Synthetic clinical document', name='synthetic.txt'):
    return SimpleUploadedFile(name, content, content_type='application/octet-stream')


class DocumentValidationTests(SimpleTestCase):
    def test_text_size_digest_and_rewind(self):
        file = text_file()
        name, content_type, size, digest = validate_document(file)
        self.assertEqual(name, 'synthetic.txt')
        self.assertEqual(content_type, 'text/plain')
        self.assertEqual(size, len(file.read()))
        self.assertEqual(len(digest), 64)
        file.seek(0)
        self.assertEqual(file.read(), b'Synthetic clinical document')

    def test_unsupported_empty_oversize_and_false_signatures_rejected(self):
        for file in [text_file(name='synthetic.exe'), text_file(b''), text_file(b'x' * (MAX_DOCUMENT_BYTES + 1)),
                     text_file(b'<html>synthetic</html>', 'synthetic.pdf'), text_file(b'not an image', 'synthetic.png'),
                     text_file(b'\x00\x01', 'synthetic.csv')]:
            with self.subTest(name=file.name), self.assertRaises(HttpError) as error:
                validate_document(file)
            self.assertEqual(error.exception.status_code, 400)

    def test_real_pdf_and_image_accepted(self):
        from reportlab.pdfgen.canvas import Canvas
        from PIL import Image
        pdf = io.BytesIO()
        canvas = Canvas(pdf); canvas.drawString(10, 10, 'Synthetic fixture'); canvas.save()
        self.assertEqual(validate_document(text_file(pdf.getvalue(), 'synthetic.pdf'))[1], 'application/pdf')
        image = io.BytesIO(); Image.new('RGB', (2, 2)).save(image, format='PNG')
        self.assertEqual(validate_document(text_file(image.getvalue(), 'synthetic.png'))[1], 'image/png')
        with self.assertRaises(HttpError):
            validate_document(text_file(image.getvalue(), 'synthetic.jpg'))

    def package(self, extra=None, xml=b'<document/>'):
        buf = io.BytesIO()
        with ZipFile(buf, 'w') as zipped:
            zipped.writestr('[Content_Types].xml', b'<Types/>')
            zipped.writestr('word/document.xml', xml)
            for name, value in (extra or {}).items():
                zipped.writestr(name, value)
        return text_file(buf.getvalue(), 'synthetic.docx')

    def test_office_package_signature_and_dangerous_entries(self):
        self.assertIn('wordprocessingml', validate_document(self.package())[1])
        for file in [self.package({'word/vbaProject.bin': b'synthetic'}), self.package({'../escape.xml': b'<x/>'}),
                     self.package(xml=b'<!DOCTYPE x [<!ENTITY y "synthetic">]><x/>')]:
            with self.assertRaises(HttpError):
                validate_document(file)

    def test_filename_and_public_storage_guard(self):
        self.assertEqual(validate_document(text_file(name='folder\\synthetic.txt'))[0], 'synthetic.txt')
        with override_settings(MEDIA_ROOT='/tmp/synthetic-public', CLIENT_DOCUMENT_ROOT='/tmp/synthetic-public/files'):
            with self.assertRaises(ValueError):
                PrivateDocumentStorage()


class ClientDocumentTests(TestCase):
    """Run against a disposable DB. These test row isolation, not PG schema routing."""
    def setUp(self):
        # bulk_create deliberately avoids TenantMixin schema provisioning: fixtures
        # stay in the test database/schema chosen by the test runner.
        self.org, self.other_org = Organization.objects.bulk_create([
            Organization(name='Synthetic A', slug='synthetic-a', schema_name='synthetic_a'),
            Organization(name='Synthetic B', slug='synthetic-b', schema_name='synthetic_b'),
        ])
        self.context = tenant_context(self.org.pk); self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.admin = User.objects.create(email='synthetic-admin@example.test', role='admin', organization=self.org, external_admin_id=100)
        self.staff = User.objects.create(email='synthetic-staff@example.test', role='staff', organization=self.org, external_admin_id=100)
        self.reporting = User.objects.create(email='synthetic-reporting@example.test', role='reporting', organization=self.org)
        self.client = Client.objects.create(first_name='Synthetic', last_name='A', external_admin_id=100)
        self.unassigned = Client.objects.create(first_name='Synthetic', last_name='B', external_admin_id=100)
        self.other_practice = Client.objects.create(first_name='Synthetic', last_name='C', external_admin_id=200)
        ClientStaffAssignment.objects.create(client=self.client, user=self.staff)
        self.temp = tempfile.TemporaryDirectory(prefix='dcm-document-tests-')
        self.addCleanup(self.temp.cleanup)
        self.settings = override_settings(CLIENT_DOCUMENT_ROOT=Path(self.temp.name) / 'private', MEDIA_ROOT=Path(self.temp.name) / 'public')
        self.settings.enable(); self.addCleanup(self.settings.disable)
        self.storage = PrivateDocumentStorage()
        storage_patch = patch.object(ClientDocument._meta.get_field('file'), 'storage', self.storage)
        storage_patch.start(); self.addCleanup(storage_patch.stop)
        self.request = SimpleNamespace(user=self.admin, tenant=self.org)

    def upload(self, **changes):
        values = dict(client_id=self.client.pk, file=text_file(), upload_id=uuid.uuid4(), title='Synthetic title', category='intake', folder_id=None)
        values.update(changes)
        return api.upload_document(self.request, **values)

    def listing(self, **changes):
        values = dict(client_id=self.client.pk, search='', category='', folder_id=None, state='active', offset=0, limit=50)
        values.update(changes)
        return api.documents(self.request, **values)

    def test_upload_private_download_and_metadata_only_response(self):
        status, data = self.upload()
        self.assertEqual(status, 201)
        document = ClientDocument.objects.get(pk=data['id'])
        self.assertTrue(Path(document.file.path).exists())
        self.assertTrue(Path(document.file.path).is_relative_to(Path(self.temp.name) / 'private'))
        self.assertNotIn('synthetic.txt', document.file.name)
        with self.assertRaises(NotImplementedError):
            _ = document.file.url
        self.assertTrue({'file', 'sha256', 'upload_id', 'storage_key'}.isdisjoint(data))
        response = api.download_document(self.request, self.client.pk, document.pk)
        self.assertEqual(b''.join(response.streaming_content), b'Synthetic clinical document')
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        response.close()
        logs = list(AuditLog.objects.filter(model='ClientDocument').values_list('changes', flat=True))
        self.assertEqual([log['event'] for log in reversed(logs)], ['uploaded', 'downloaded'])
        self.assertNotIn('synthetic.txt', json.dumps(logs))
        self.assertNotIn('Synthetic title', json.dumps(logs))

    def test_same_upload_id_is_idempotent_conflicts_do_not_replace_file(self):
        upload_id = uuid.uuid4()
        _, data = self.upload(upload_id=upload_id)
        self.assertEqual(self.upload(upload_id=upload_id)[0], 200)
        self.assertEqual(ClientDocument.objects.count(), 1)
        self.assertEqual(len(list(Path(self.temp.name).rglob('*.txt'))), 1)
        with self.assertRaises(HttpError) as error:
            self.upload(upload_id=upload_id, file=text_file(b'Different synthetic content'))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(ClientDocument.objects.get(pk=data['id']).title, 'Synthetic title')

    def test_archive_preserves_bytes_and_restore_reenables_download(self):
        _, data = self.upload()
        path = ClientDocument.objects.get(pk=data['id']).file.path
        api.archive_document(self.request, self.client.pk, data['id'])
        self.assertTrue(Path(path).exists())
        self.assertEqual(self.listing()['total'], 0)
        self.assertEqual(self.listing(state='archived')['total'], 1)
        with self.assertRaises(HttpError) as error:
            api.download_document(self.request, self.client.pk, data['id'])
        self.assertEqual(error.exception.status_code, 409)
        api.restore_document(self.request, self.client.pk, data['id'])
        self.assertEqual(self.listing()['total'], 1)
        self.assertTrue(Path(path).exists())

    def test_folder_organization_search_and_pagination(self):
        _, folder = api.create_folder(self.request, self.client.pk, FolderInput(name=' Intake '))
        with self.assertRaises(HttpError) as error:
            api.create_folder(self.request, self.client.pk, FolderInput(name='intake'))
        self.assertEqual(error.exception.status_code, 409)
        _, data = self.upload(folder_id=folder.pk)
        api.update_document(self.request, self.client.pk, data['id'], DocumentUpdate(title='Synthetic consent', category='consent', folder_id=folder.pk))
        api.rename_folder(self.request, self.client.pk, folder.pk, FolderInput(name='Forms'))
        listing = self.listing(search='consent', category='consent', folder_id=folder.pk)
        self.assertEqual(listing['total'], 1)
        self.assertEqual(listing['items'][0]['folder_name'], 'Forms')
        self.assertEqual(self.listing(folder_id=0)['total'], 0)
        self.assertEqual(self.listing(offset=1)['items'], [])

    def test_other_client_folder_and_document_are_rejected(self):
        _, data = self.upload()
        other_folder = ClientDocumentFolder.objects.create(client=self.unassigned, name='Synthetic other folder')
        with self.assertRaises(HttpError) as error:
            api.update_document(self.request, self.client.pk, data['id'], DocumentUpdate(title='Synthetic moved', category='other', folder_id=other_folder.pk))
        self.assertEqual(error.exception.status_code, 404)
        with self.assertRaises(HttpError):
            api.download_document(self.request, self.unassigned.pk, data['id'])
        self.assertIsNone(ClientDocument.objects.get(pk=data['id']).folder_id)

    def test_assigned_staff_view_only_and_revocation(self):
        _, data = self.upload()
        self.request.user = self.staff
        self.assertEqual(self.listing()['total'], 1)
        with self.assertRaises(HttpError) as error:
            self.upload()
        self.assertEqual(error.exception.status_code, 403)
        for call in [api.archive_document, api.restore_document]:
            with self.assertRaises(HttpError) as error:
                call(self.request, self.client.pk, data['id'])
            self.assertEqual(error.exception.status_code, 403)
        ClientStaffAssignment.objects.filter(client=self.client, user=self.staff).update(is_active=False)
        with self.assertRaises(HttpError) as error:
            api.download_document(self.request, self.client.pk, data['id'])
        self.assertEqual(error.exception.status_code, 404)

    def test_permission_revocation_reporting_and_practice_boundaries(self):
        _, data = self.upload()
        for client in [self.other_practice]:
            with self.assertRaises(HttpError) as error:
                self.listing(client_id=client.pk)
            self.assertEqual(error.exception.status_code, 404)
        self.request.user = self.reporting
        with self.assertRaises(HttpError) as error:
            self.listing()
        self.assertEqual(error.exception.status_code, 403)
        self.request.user = self.staff
        RolePermission.objects.create(organization=self.org, role='staff', permissions={'client_documents': False})
        with self.assertRaises(HttpError) as error:
            api.download_document(self.request, self.client.pk, data['id'])
        self.assertEqual(error.exception.status_code, 403)
        self.request.user = self.admin
        with tenant_context(self.other_org.pk):
            with self.assertRaises(HttpError) as error:
                api.download_document(self.request, self.client.pk, data['id'])
            self.assertEqual(error.exception.status_code, 404)

    def test_audit_failure_rolls_back_new_upload_and_cleans_only_new_blob(self):
        with patch.object(api, '_audit', side_effect=RuntimeError('Synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.upload()
        self.assertEqual(ClientDocument.objects.count(), 0)
        self.assertEqual(list(Path(self.temp.name).rglob('*.txt')), [])
