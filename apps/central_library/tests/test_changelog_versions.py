"""Synthetic version import checks without database or external service access."""
from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import patch

from apps.central_library.changelog_notepad import import_notepad, parse_notepad
from apps.central_library.management.commands.import_changelog_notepad import parse_notepad as parse_legacy

NOTE = '''## example | 2026-11-17 | Example
Version: v2.43.0
Status: Draft

**Improvements**
- Synthetic update.
'''


class VersionTests(TestCase):
    def test_metadata_in_both_formats(self):
        for parser, text in [(parse_notepad, NOTE), (parse_legacy, NOTE.replace('example | 2026-11-17 |', '2026-11-17 —'))]:
            entry, = parser(text)
            self.assertEqual(entry['version'], 'v2.43.0')
            self.assertNotIn('Version:', entry['body'])
            self.assertNotIn('Status:', entry['body'])

    def test_invalid_metadata(self):
        for value in ['', 'x' * 41, 'v1\nVersion: v2']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_notepad(NOTE.replace('v2.43.0', value))

    def test_legacy_published_status(self):
        entry, = parse_legacy(NOTE.replace('example | 2026-11-17 |', '2026-11-17 —').replace('Draft', 'Published'))
        self.assertTrue(entry['is_published'])

    def test_import_preserves_or_updates_only_expected_versions(self):
        parsed, = parse_notepad(NOTE)
        for previous, expected in [('', 1), ('v2026.11.17', 1), ('v1.2.3', 0), ('v2.43.0', 0), ('v1.33.0', 1), ('v1.32.0', 0), ('v2026.11.16', 0)]:
            with self.subTest(previous=previous), \
                    patch('apps.central_library.models.ChangelogEntry.objects') as manager, \
                    patch('django.db.transaction.atomic', return_value=nullcontext()), \
                    patch('django_tenants.utils.schema_context', return_value=nullcontext()), \
                    patch('django_tenants.utils.get_public_schema_name', return_value='public'), \
                    patch('pathlib.Path.read_text', return_value=NOTE):
                manager.values.return_value = [{**parsed, 'id': 1, 'version': previous, 'body': '<p>Edited</p>'}]
                manager.filter.return_value.update.return_value = 1
                result = import_notepad()
                self.assertEqual(result, dict(created=0, updated=expected, skipped=1 - expected))
                if expected:
                    manager.filter.assert_called_once_with(pk=1, version=previous)
                    manager.filter.return_value.update.assert_called_once_with(version='v2.43.0')
                else:
                    manager.filter.assert_not_called()
                manager.get_or_create.assert_not_called()
