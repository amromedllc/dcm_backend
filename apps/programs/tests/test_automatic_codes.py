from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.programs.schemas import (
    ProgramCodeSchema, TargetCodeSchema, ProgramSchema, ProgramListSchema,
    OrgProgramSchema, TargetSchema, TargetSummarySchema,
    ProgramCreateRequest, TargetCreateRequest,
)


class AutomaticCodeTests(SimpleTestCase):
    def test_codes_are_generated_from_identity_for_models_and_dicts(self):
        for schema, prefix in ((ProgramCodeSchema, 'PRG'), (TargetCodeSchema, 'TAR')):
            for record_id in (1, 42, 10000):
                for obj in ({'id': record_id, 'code': 'user-supplied'}, SimpleNamespace(id=record_id)):
                    self.assertEqual(schema.from_orm(obj).code, f'{prefix}-{record_id:04d}')

    def test_all_program_and_target_responses_include_codes(self):
        for schema in (ProgramSchema, ProgramListSchema, OrgProgramSchema, TargetSchema, TargetSummarySchema):
            self.assertIn('code', schema.model_fields)

    def test_create_requests_do_not_accept_user_defined_codes(self):
        program = ProgramCreateRequest(client_id=1, name='Program', code='CUSTOM')
        target = TargetCreateRequest(name='Target', code='CUSTOM')
        self.assertNotIn('code', program.model_dump())
        self.assertNotIn('code', target.model_dump())
