from unittest import mock

from django.test import SimpleTestCase
from ninja.errors import HttpError
from pydantic import ValidationError

from apps.programs import api
from apps.programs.schemas import CentralProgramRequest, CentralTargetRequest


class CentralProgramCreationTests(SimpleTestCase):
    def test_accepts_program_fields_and_target_names_together(self):
        data = CentralProgramRequest(
            name='Communication', category='skill_acquisition',
            treatment_area='Language', tags=['Communication'], setting_ids=[1, 2],
            targets=[{'name': 'Request help'}, {'name': 'Request break'}],
        )
        self.assertEqual([target.name for target in data.targets], ['Request help', 'Request break'])
        self.assertEqual(data.targets[0].measurement_type, 'discrete_trial')
        self.assertEqual(data.setting_ids, [1, 2])

    def test_invalid_target_name_rejects_entire_request(self):
        with self.assertRaises(ValidationError):
            CentralProgramRequest(name='Communication', targets=[{'name': '   '}])

    def test_existing_callers_can_still_create_without_targets(self):
        self.assertEqual(CentralProgramRequest(name='Communication').targets, [])

    def test_invalid_target_setting_is_checked_before_writing_target(self):
        with mock.patch.object(api, 'validate_setting_links', side_effect=HttpError(400, 'Unknown setting')), \
             mock.patch.object(api.CentralTarget.objects, 'create') as create:
            with self.assertRaises(HttpError):
                api._create_central_target(mock.Mock(), CentralTargetRequest(name='Request help', setting_ids=[999]))
            create.assert_not_called()
