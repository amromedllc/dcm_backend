"""Client-program and note endpoints enforce the same menu permissions the frontend hides buttons with."""
from unittest import mock

from django.test import SimpleTestCase
from ninja.errors import HttpError

from apps.notes import api as notes_api
from apps.programs import api as programs_api

REQ = mock.MagicMock()


class Denied(Exception):
    pass


def _call(module, name, *args):
    """Call an endpoint with every permission denied; return the permission keys it asked for."""
    asked: list[str] = []

    def deny(request, permission):
        asked.append(permission)
        raise Denied

    with mock.patch.object(module, 'require_permission', side_effect=deny), \
         mock.patch('apps.accounts.permissions.user_has_permission', return_value=False), \
         mock.patch('apps.accounts.permissions.resolve_permission_organization', return_value=None):
        try:
            getattr(module, name)(REQ, *args)
        except Denied:
            return asked, 'denied'
        except HttpError as exc:
            return asked, exc.status_code
    return asked, 'allowed'


class ClientProgramWritePermissionTests(SimpleTestCase):
    def test_creating_needs_the_create_permission(self):
        for name, args in [('create_program', (mock.MagicMock(),)), ('copy_program_to_client', (1, mock.MagicMock()))]:
            with self.subTest(endpoint=name):
                asked, outcome = _call(programs_api, name, *args)
                self.assertEqual((asked, outcome), (['client_programs_create'], 'denied'))

    def test_changing_an_existing_program_is_refused_without_either_programs_permission(self):
        for name, args in [
            ('update_program', (1, mock.MagicMock())), ('archive_program', (1,)), ('delete_program_permanently', (1,)),
            ('create_target', (1, mock.MagicMock())), ('update_target', (1, mock.MagicMock())), ('delete_target', (1,)),
            ('bulk_update_targets', (1, mock.MagicMock())), ('reorder_targets', (1, mock.MagicMock())),
            ('create_module', (1, mock.MagicMock())), ('delete_module', (1, 1)), ('upload_program_material', (1,)),
            ('delete_program_material', (1,)),
        ]:
            with self.subTest(endpoint=name):
                asked, outcome = _call(programs_api, name, *args)
                self.assertEqual(outcome, 403)

    def test_library_programs_use_org_permissions_and_client_programs_use_client_permissions(self):
        for is_template, edit_key, delete_key in ((True, 'org_programs_edit', 'org_programs_delete'),
                                                  (False, 'client_programs_edit', 'client_programs_delete')):
            program = mock.MagicMock(is_template=is_template)
            for action, key in (('edit', edit_key), ('delete', delete_key)):
                with self.subTest(template=is_template, action=action):
                    asked: list[str] = []
                    with mock.patch.object(programs_api, 'require_permission', side_effect=lambda r, p: asked.append(p)):
                        programs_api._check_program_write(REQ, program, action)
                    self.assertEqual(asked, [key])


class ReadPermissionTests(SimpleTestCase):
    def test_program_reads_are_refused_without_any_program_permission(self):
        for name, args in [
            ('list_programs', (1,)), ('get_program', (1,)), ('list_targets', (1,)), ('get_target', (1,)),
            ('list_program_materials', (1,)), ('list_modules', (1,)),
            ('target_history', (1,)), ('target_prompt_level_history', (1,)), ('client_target_history', (1,)),
            ('list_org_programs', ()), ('get_org_program', (1,)), ('list_program_folders', ()),
        ]:
            with self.subTest(endpoint=name):
                asked, outcome = _call(programs_api, name, *args)
                self.assertEqual(outcome, 403)

    def test_note_reads_and_submit_are_refused_without_permission(self):
        for name, args in [
            ('list_notes', ()), ('get_note', (1,)), ('list_signatures', (1,)), ('list_assignments', (1,)),
            ('list_note_templates', ()), ('get_note_template', (1,)),
        ]:
            with self.subTest(endpoint=name):
                asked, outcome = _call(notes_api, name, *args)
                self.assertEqual(outcome, 403)
        asked, outcome = _call(notes_api, 'submit', 1)
        self.assertEqual(asked, ['note_submit'])
