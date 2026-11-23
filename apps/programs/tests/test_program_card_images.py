from io import BytesIO
from types import SimpleNamespace
from unittest import mock
from django.test import SimpleTestCase
from PIL import Image
from shared.program_images import program_card_image_url


class ProgramCardImageTests(SimpleTestCase):
    def request(self):
        return SimpleNamespace(build_absolute_uri=lambda url: 'https://app.example' + url if url.startswith('/') else url)

    def test_remote_storage_gets_small_variant_with_its_own_signed_url(self):
        source = BytesIO()
        Image.new('RGB', (1600, 1600), 'blue').save(source, 'PNG')
        source.seek(0)
        storage = mock.Mock()
        storage.exists.return_value = False
        storage.open.return_value = source
        storage.save.side_effect = lambda name, content: name
        storage.url.return_value = 'https://cdn.example/card.webp?signature=variant'
        field = SimpleNamespace(storage=storage, name='program.png', url='https://cdn.example/original.png?signature=original')
        self.assertEqual(program_card_image_url(self.request(), field), storage.url.return_value)
        name, saved = storage.save.call_args.args
        self.assertEqual(name, 'program.png.card-v2.webp')
        with Image.open(BytesIO(saved.read())) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertLessEqual(image.width, 800)
            self.assertLessEqual(image.height, 600)

    def test_existing_variant_is_reused_without_reading_original(self):
        storage = mock.Mock()
        storage.exists.return_value = True
        storage.url.return_value = '/media/card.webp'
        field = SimpleNamespace(storage=storage, name='program.png')
        self.assertEqual(program_card_image_url(self.request(), field), 'https://app.example/media/card.webp')
        storage.open.assert_not_called()
        storage.save.assert_not_called()

    def test_storage_failure_falls_back_to_original(self):
        storage = mock.Mock()
        storage.exists.side_effect = OSError('Unavailable')
        field = SimpleNamespace(storage=storage, name='program.png', url='/media/program.png')
        with self.assertLogs('shared.program_images', level='WARNING'):
            self.assertEqual(program_card_image_url(self.request(), field), 'https://app.example/media/program.png')

    def test_missing_image(self):
        self.assertIsNone(program_card_image_url(self.request(), None))
