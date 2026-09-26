"""Program and target input limits, and template references, are rejected cleanly (400/422), not as database errors."""
from unittest import mock

from django.test import SimpleTestCase
from ninja.errors import HttpError
from pydantic import ValidationError

from apps.programs import api as programs_api
from apps.programs.schemas import (
    OrgProgramCreateRequest, ProgramCreateRequest, ProgramUpdateRequest, TargetCreateRequest, TargetUpdateRequest,
)


class ProgramSchemaLimitTests(SimpleTestCase):
    def test_name_is_trimmed_required_and_capped(self):
        self.assertEqual(ProgramCreateRequest(client_id=1, name='  Manding  ').name, 'Manding')
        for bad in ('', '   ', 'x' * 201):
            with self.assertRaises(ValidationError):
                ProgramCreateRequest(client_id=1, name=bad)
            with self.assertRaises(ValidationError):
                OrgProgramCreateRequest(name=bad)
            with self.assertRaises(ValidationError):
                TargetCreateRequest(name=bad)
        with self.assertRaises(ValidationError):
            ProgramUpdateRequest(name='y' * 201)
        with self.assertRaises(ValidationError):
            TargetUpdateRequest(name='   ')

    def test_200_characters_is_allowed(self):
        self.assertEqual(len(ProgramCreateRequest(client_id=1, name='x' * 200).name), 200)

    def test_treatment_area_tags_and_text_are_capped(self):
        with self.assertRaises(ValidationError):
            ProgramCreateRequest(client_id=1, name='X', treatment_area='a' * 201)
        with self.assertRaises(ValidationError):
            ProgramCreateRequest(client_id=1, name='X', tags=['t'] * 51)
        with self.assertRaises(ValidationError):
            ProgramCreateRequest(client_id=1, name='X', tags=['t' * 101])
        with self.assertRaises(ValidationError):
            OrgProgramCreateRequest(name='X', objective='o' * 50001)
        with self.assertRaises(ValidationError):
            ProgramUpdateRequest(instructions_html='h' * 50001)


class TemplateRefTests(SimpleTestCase):
    def _qs(self, exists: bool):
        qs = mock.MagicMock()
        qs.filter.return_value.exists.return_value = exists
        return qs

    def test_unknown_template_ids_are_a_400(self):
        with mock.patch.object(programs_api, '_settings_qs', return_value=self._qs(False)):
            with self.assertRaises(HttpError) as ctx:
                programs_api._validate_template_refs(None, 999, None)
            self.assertEqual(ctx.exception.status_code, 400)
            with self.assertRaises(HttpError):
                programs_api._validate_template_refs(None, None, 999)

    def test_known_or_missing_ids_pass(self):
        with mock.patch.object(programs_api, '_settings_qs', return_value=self._qs(True)):
            programs_api._validate_template_refs(None, 1, 2)
        programs_api._validate_template_refs(None, None, None)  # nothing to check, no query made


class RequireTargetsTests(SimpleTestCase):
    def test_program_needs_at_least_one_target(self):
        with self.assertRaises(HttpError) as ctx:
            programs_api._require_targets('skill_acquisition', [])
        self.assertEqual(ctx.exception.status_code, 400)
        programs_api._require_targets('skill_acquisition', [object()])

    def test_instructions_only_cannot_have_targets_and_does_not_need_them(self):
        programs_api._require_targets('instructions_only', [])
        with self.assertRaises(HttpError):
            programs_api._require_targets('instructions_only', [object()])

    def test_create_requests_accept_embedded_targets(self):
        data = ProgramCreateRequest(client_id=1, name='X', targets=[{'name': 'Asks for snack'}])
        self.assertEqual(data.targets[0].name, 'Asks for snack')
        self.assertEqual(data.targets[0].measurement_type, 'discrete_trial')
        with self.assertRaises(ValidationError):
            OrgProgramCreateRequest(name='X', targets=[{'name': ''}])


class ProgramListSchemaTests(SimpleTestCase):
    def test_list_schema_exposes_archived_at(self):
        # The Programs page splits Active/Archived on this field; if the list schema drops it,
        # every archived program shows up as active.
        from apps.programs.schemas import ProgramListSchema
        self.assertIn('archived_at', ProgramListSchema.model_fields)


class PermanentDeleteTests(SimpleTestCase):
    def _program(self, archived: bool):
        program = mock.MagicMock()
        program.archived_at = '2026-01-01' if archived else None
        return program

    def _call(self, program, has_data=False):
        with mock.patch.object(programs_api, '_require_supervisor'), \
             mock.patch.object(programs_api, '_get_program_or_404', return_value=program), \
             mock.patch.object(programs_api, '_last_run_by_target', return_value={1: 'x'} if has_data else {}):
            return programs_api.delete_program_permanently(mock.MagicMock(), 1)

    def test_must_be_archived_first(self):
        program = self._program(archived=False)
        with self.assertRaises(HttpError) as ctx:
            self._call(program)
        self.assertEqual(ctx.exception.status_code, 400)
        program.delete.assert_not_called()

    def test_program_with_recorded_data_is_refused(self):
        program = self._program(archived=True)
        with self.assertRaises(HttpError) as ctx:
            self._call(program, has_data=True)
        self.assertEqual(ctx.exception.status_code, 409)
        program.delete.assert_not_called()

    def test_archived_program_without_data_is_deleted(self):
        program = self._program(archived=True)
        with mock.patch.object(programs_api.transaction, 'atomic'):
            self.assertEqual(self._call(program), (204, None))
        program.delete.assert_called_once()
