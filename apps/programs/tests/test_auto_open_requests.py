from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from ninja.errors import HttpError
from pydantic import ValidationError

from apps.programs import api
from apps.programs.schemas import WorkflowTemplateCreateRequest, WorkflowTemplateUpdateRequest


class AutoOpenRequestTests(SimpleTestCase):
    def test_create_and_patch_validate_auto_open_json(self):
        for schema in (WorkflowTemplateCreateRequest, WorkflowTemplateUpdateRequest):
            for invalid in ({'auto_open_status': 'mastered'}, {'auto_open_max_targets': 0},
                            {'auto_open_direction': 'invalid'}, {'auto_open_waiting': 'true'}):
                with self.subTest(schema=schema, invalid=invalid), self.assertRaises(ValidationError):
                    schema(name='Synthetic workflow', phases=[{'phase': 'mastered', **invalid}])

    def test_create_and_patch_require_existing_workflow_permissions_before_access(self):
        request = SimpleNamespace(user=SimpleNamespace())
        for endpoint, args, permission in (
            (api.create_workflow_template, (WorkflowTemplateCreateRequest(
                name='Synthetic workflow', phases=[{'phase': 'mastered', 'auto_open_waiting': True}],
            ),), 'settings_workflows_create'),
            (api.update_workflow_template, (123, WorkflowTemplateUpdateRequest(
                phases=[{'phase': 'mastered', 'auto_open_waiting': True}],
            )), 'settings_workflows_edit'),
        ):
            with self.subTest(endpoint=endpoint.__name__), patch.object(
                api, '_require_settings_permission', side_effect=HttpError(403, 'Denied'),
            ) as require, patch.object(api, '_settings_qs') as query:
                with self.assertRaises(HttpError) as error:
                    endpoint(request, *args)
                self.assertEqual(error.exception.status_code, 403)
                require.assert_called_once_with(request, permission)
                query.assert_not_called()
