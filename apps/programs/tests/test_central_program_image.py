import io
from types import SimpleNamespace
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from ninja.errors import HttpError
from PIL import Image

from apps.programs import api


class CentralProgramImageTests(SimpleTestCase):
    def test_non_superadmin_cannot_upload(self):
        request = SimpleNamespace(user=SimpleNamespace(is_superuser=False))
        with mock.patch.object(api, '_get_superadmin_central_program_or_404') as lookup:
            with self.assertRaises(HttpError) as error:
                api.superadmin_upload_central_program_image(request, 1, None)
            self.assertEqual(error.exception.status_code, 403)
            lookup.assert_not_called()

    def test_valid_image_is_saved_and_returned(self):
        buffer = io.BytesIO()
        Image.new('RGB', (2, 2)).save(buffer, format='PNG')
        upload = SimpleUploadedFile('program.png', buffer.getvalue(), content_type='image/png')
        request = SimpleNamespace(user=SimpleNamespace(is_superuser=True))
        program = mock.Mock()
        with mock.patch.object(api, '_get_superadmin_central_program_or_404', return_value=program), \
             mock.patch.object(api, '_serialize_superadmin_central_program', return_value={'image_url': '/media/program.png'}):
            response = api.superadmin_upload_central_program_image(request, 1, upload)
        self.assertIs(program.image, upload)
        program.save.assert_called_once_with(update_fields=['image', 'updated_at'])
        self.assertEqual(response['image_url'], '/media/program.png')

    def test_invalid_image_is_rejected_without_saving(self):
        request = SimpleNamespace(user=SimpleNamespace(is_superuser=True))
        for upload in (
            SimpleUploadedFile('bad.png', b'not an image', content_type='image/png'),
            SimpleUploadedFile('script.svg', b'<svg/>', content_type='image/svg+xml'),
            SimpleUploadedFile('large.png', b'x' * (10 * 1024 * 1024 + 1), content_type='image/png'),
        ):
            program = mock.Mock()
            with mock.patch.object(api, '_get_superadmin_central_program_or_404', return_value=program):
                with self.assertRaises(HttpError) as error:
                    api.superadmin_upload_central_program_image(request, 1, upload)
                self.assertEqual(error.exception.status_code, 400)
                program.save.assert_not_called()
