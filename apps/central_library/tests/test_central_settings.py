from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from django.db import connection
from django.test import TestCase, SimpleTestCase, override_settings
from django_tenants.utils import schema_context
from ninja.errors import HttpError
from pydantic import ValidationError
from apps.accounts.models import User
from apps.central_library.models import CentralProgram, CentralTarget, CentralProgramSetting, CentralSettingCopy
from apps.central_library.settings_service import normalize_configuration, validate_setting_links, pull_setting
from apps.central_library.settings_api import create_setting, SettingRequest
from apps.programs.api import _clone_central_program, _clone_central_target_as_program
from apps.programs.models import Program, Target, PromptingTemplate, WorkflowTemplate, TreatmentArea, ProgramTag, ProgramDataField, TargetStatus
from apps.programs.schemas import CentralProgramConfiguration, CentralTargetConfiguration
from apps.tenants.models import Organization
from shared.tenancy import tenant_context

LEVELS = [dict(label='Independent', abbreviation='I', color='#16a34a', weight=1, is_success=True),
          dict(label='Model', abbreviation='M', color='#6366f1', weight=0)]

class ConfigurationValidationTests(SimpleTestCase):
    def test_facility_ids_and_defaults_are_rejected(self):
        for key in ['organization_id', 'created_by_id', 'program_template_ids', 'is_org_default']:
            with self.assertRaises(HttpError):
                normalize_configuration('prompting', 'Prompt', {'levels': LEVELS, key: 1})
        for schema, value in [(CentralProgramConfiguration, {'id': 1}), (CentralTargetConfiguration, {'workflow_template_id': 1})]:
            with self.assertRaises(ValidationError): schema(**value)

    def test_fading_and_success_validation(self):
        for hints in [{'decrease_match_pct': 101}, {'increase_across': 0}]:
            with self.assertRaises(HttpError):
                normalize_configuration('prompting', 'Prompt', {'levels': LEVELS, 'fading_hint_settings': hints})
        with self.assertRaises(HttpError):
            normalize_configuration('prompting', 'Prompt', {'levels': [dict(LEVELS[1])]})

class CentralSettingsImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name='Central copy A', slug='central-copy-a', schema_name='central_copy_a')
        cls.other_org = Organization.objects.create(name='Central copy B', slug='central-copy-b', schema_name='central_copy_b')
        cls.user = User.objects.create_user(email='central-copy-a@example.com', password='x', organization=cls.org, role='admin')
        cls.other_user = User.objects.create_user(email='central-copy-b@example.com', password='x', organization=cls.other_org, role='admin')
        connection.set_schema_to_public()

    def setting(self, kind, name, **config):
        return CentralProgramSetting.objects.create(kind=kind, name=name, configuration=normalize_configuration(kind, name, config))

    def setUp(self):
        self.program = CentralProgram.objects.create(name='Central program', treatment_area='Communication', tags=['Communication'])
        self.prompt = self.setting('prompting', 'Central prompting', levels=deepcopy(LEVELS), fading_hint_mode='across_sessions', fading_hint_settings={'decrease_across': 3, 'decrease_match_pct': 90})
        self.start = self.setting('target_status', 'Test Waiting', key='test_waiting')
        self.finish = self.setting('target_status', 'Test Mastered', key='test_mastered')
        self.workflow = self.setting('workflow', 'Central workflow', phases=[dict(phase='test_waiting', objective_key='increase_percent_correct', criteria={'threshold_pct': 80, 'minimum_trials': 5, 'consecutive_sessions': 3}, on_success='test_mastered')])
        self.workflow.dependencies.set([self.start, self.finish])
        self.field = self.setting('data_field', 'Caregiver note', field_type='text')
        self.program.settings.set([self.prompt, self.workflow, self.start, self.field])
        self.program.configuration = {'baseline_notes': 'Baseline note', 'custom_field_values': {str(self.field.pk): 'Imported value'}}
        self.program.save()
        self.target = CentralTarget.objects.create(program=self.program, name='Target 1', configuration={'mastery_mode': 'automatic'})

    def test_import_links_local_settings_with_fading_custom_fields_and_statuses(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            dest = _clone_central_program(self.program.pk, self.user)
            target = dest.targets.get()
            self.assertEqual(dest.prompting_template.fading_hint_mode, 'across_sessions')
            self.assertEqual(dest.prompting_template.fading_hint_settings['decrease_match_pct'], 90)
            self.assertEqual(target.prompting_template_id, dest.prompting_template_id)
            self.assertEqual(target.workflow_template_id, dest.workflow_template_id)
            self.assertEqual(target.current_prompt_level_index, 1)
            self.assertEqual(target.mastery_mode, 'automatic')
            self.assertEqual(dest.baseline_notes, 'Baseline note')
            self.assertEqual(target.status, 'test_waiting')
            field = ProgramDataField.objects.get(name='Caregiver note')
            self.assertEqual(dest.custom_field_values, {str(field.pk): 'Imported value'})
            self.assertIn(dest.pk, field.program_template_ids)
            self.assertFalse(field.enable_on_all_programs)
            self.assertTrue(TreatmentArea.objects.filter(name=dest.treatment_area).exists())
            self.assertTrue(ProgramTag.objects.filter(name__in=dest.tags).exists())
            self.assertTrue(TargetStatus.objects.filter(key='test_mastered').exists())

    def test_partial_import_preserves_edits_reuses_settings_and_honors_target_override(self):
        override = self.setting('prompting', 'Target override', levels=deepcopy(LEVELS))
        target2 = CentralTarget.objects.create(program=self.program, name='Target 2')
        target2.settings.add(override)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            dest = _clone_central_target_as_program(self.program.pk, self.target.pk, self.user)
            prompt = dest.prompting_template
            prompt.description = 'Edited at facility'
            prompt.save()
            copy = _clone_central_program(self.program.pk, self.user)
            self.assertEqual(copy.pk, dest.pk)
            self.assertEqual(copy.targets.count(), 2)
            self.assertEqual(copy.prompting_template.description, 'Edited at facility')
            self.assertNotEqual(copy.targets.get(name='Target 2').prompting_template_id, prompt.pk)
            self.assertEqual(PromptingTemplate.objects.count(), 2)
            with self.assertRaises(HttpError) as error: _clone_central_program(self.program.pk, self.user)
            self.assertEqual(error.exception.status_code, 409)

    def test_name_conflicts_do_not_overwrite_existing_facility_setting(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            existing = PromptingTemplate.objects.create(name=self.prompt.name, levels=LEVELS, description='Local', created_by=self.user)
            imported = pull_setting(self.prompt, self.user)
            self.assertNotEqual(existing.pk, imported.pk)
            self.assertEqual(imported.name, self.prompt.name + ' (2)')
            existing.refresh_from_db()
            self.assertEqual(existing.description, 'Local')
            self.assertEqual(pull_setting(self.prompt, self.user).pk, imported.pk)

    def test_different_central_template_names_reuse_same_facility_definition(self):
        duplicate = self.setting('prompting', 'Another program prompt', **deepcopy(self.prompt.configuration))
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            first = pull_setting(self.prompt, self.user)
            second = pull_setting(duplicate, self.user)
            self.assertEqual(first.pk, second.pk)
            self.assertEqual(PromptingTemplate.objects.count(), 1)
            self.assertEqual(CentralSettingCopy.objects.filter(local_id=first.pk, organization=self.org).count(), 2)

    def test_matching_existing_facility_template_is_reused(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            existing = PromptingTemplate.objects.create(name='Our reusable prompt', created_by=self.user,
                                                       **deepcopy(self.prompt.configuration))
            imported = pull_setting(self.prompt, self.user)
            self.assertEqual(imported.pk, existing.pk)
            self.assertEqual(imported.name, 'Our reusable prompt')
            self.assertEqual(PromptingTemplate.objects.count(), 1)

    def test_facilities_get_independent_copies(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            first = pull_setting(self.prompt, self.user)
            first.description = 'Facility A'
            first.save()
        with schema_context(self.other_org.schema_name), tenant_context(self.other_org.pk):
            second = pull_setting(self.prompt, self.other_user)
            self.assertEqual(second.organization_id, self.other_org.pk)
            self.assertNotEqual(second.description, 'Facility A')
        self.assertEqual(CentralSettingCopy.objects.filter(setting=self.prompt).count(), 2)

    def test_write_failure_rolls_back_settings_program_and_mapping(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            before = CentralSettingCopy.objects.count()
            with patch.object(Target.objects, 'create', side_effect=RuntimeError('failure')):
                with self.assertRaises(RuntimeError): _clone_central_program(self.program.pk, self.user)
            self.assertFalse(Program.objects.exists())
            self.assertFalse(PromptingTemplate.objects.exists())
            self.assertFalse(WorkflowTemplate.objects.exists())
            self.assertEqual(CentralSettingCopy.objects.count(), before)

    def test_invalid_links_and_non_superadmin_writes_are_rejected(self):
        with self.assertRaises(HttpError): validate_setting_links([self.field.pk], target=True)
        with self.assertRaises(HttpError): validate_setting_links([self.prompt.pk, self.prompt.pk])
        with self.assertRaises(HttpError) as error:
            create_setting(SimpleNamespace(user=self.user), SettingRequest(kind='tag', name='No access'))
        self.assertEqual(error.exception.status_code, 403)

    def test_tpms_facilities_in_one_organization_do_not_share_settings(self):
        first_user = User.objects.create_user(email='central-facility-a@example.com', password='x', organization=self.org, role='admin', external_admin_id=7001)
        second_user = User.objects.create_user(email='central-facility-b@example.com', password='x', organization=self.org, role='admin', external_admin_id=7002)
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            first = pull_setting(self.prompt, first_user)
            second = pull_setting(self.prompt, second_user)
            self.assertNotEqual(first.pk, second.pk)
            self.assertEqual(first.created_by_id, first_user.pk)
            self.assertEqual(second.created_by_id, second_user.pk)
            self.assertEqual(pull_setting(self.prompt, first_user).pk, first.pk)
            self.assertEqual(pull_setting(self.prompt, second_user).pk, second.pk)

    @override_settings(ALLOWED_HOSTS=['central-settings.test'])
    def test_http_authoring_and_import_return_linked_settings(self):
        import json
        from django.test import Client
        from apps.accounts.auth import create_access_token
        from apps.tenants.models import Domain
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        Domain.objects.create(domain='central-settings.test', tenant=self.org, is_primary=True)
        client = Client(HTTP_HOST='central-settings.test', HTTP_AUTHORIZATION='Bearer ' + create_access_token(self.user, self.org.pk))
        def post(path, data):
            try:
                response = client.post('/api/v1' + path, data=json.dumps(data), content_type='application/json')
                self.assertEqual(response.status_code, 201, response.content)
                return response.json()
            finally:
                connection.set_schema_to_public()
        tag = post('/superadmin/central-settings', {'kind': 'tag', 'name': 'HTTP tag', 'configuration': {'color': '#123456'}})
        program = post('/superadmin/central-programs', {'name': 'HTTP program', 'setting_ids': [self.prompt.pk, self.workflow.pk, tag['id']]})
        self.assertIn(self.prompt.pk, program['setting_ids'])
        post(f'/superadmin/central-programs/{program["id"]}/targets', {'name': 'HTTP target', 'configuration': {'mastery_mode': 'automatic'}})
        imported = post(f'/central-programs/{program["id"]}/import', {})
        self.assertIsNotNone(imported['prompting_template_id'])
        self.assertIsNotNone(imported['workflow_template_id'])
        self.assertIn('HTTP tag', imported['tags'])
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            target = Program.objects.get(pk=imported['id']).targets.get()
            self.assertEqual(target.prompting_template_id, imported['prompting_template_id'])

    def test_workbook_import_keeps_ids_links_settings_and_rolls_back_failures(self):
        import io
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        from django.db import transaction
        path = Path(__file__).resolve().parent.parent / 'data' / 'progressly_corrected_catalog.json'
        old = CentralProgram.objects.create(source_id='PRG-0001', name='Before update')
        old_target = CentralTarget.objects.create(source_id='PRG-0001-T01', program=old, name='Before target')
        before = (CentralProgram.objects.count(), CentralTarget.objects.count(), CentralProgramSetting.objects.count())
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(CentralTarget.objects, 'bulk_create', side_effect=RuntimeError('simulated failure')):
                with self.assertRaises(RuntimeError):
                    call_command('import_central_catalog', str(path), replace=True, backup_file=str(Path(directory)/'failure.json'), stdout=io.StringIO())
            self.assertEqual((CentralProgram.objects.count(), CentralTarget.objects.count(), CentralProgramSetting.objects.count()), before)
            old.refresh_from_db()
            self.assertEqual(old.name, 'Before update')
            with transaction.atomic():
                call_command('import_central_catalog', str(path), replace=True, backup_file=str(Path(directory)/'success.json'), stdout=io.StringIO())
                old.refresh_from_db()
                old_target.refresh_from_db()
                self.assertEqual(old.name, 'Requests preferred item')
                self.assertEqual(old_target.name, 'Requests preferred food')
                self.assertEqual(CentralProgram.objects.count(), 163)
                self.assertEqual(CentralTarget.objects.count(), 489)
                self.assertEqual(CentralProgram.settings.through.objects.count(), 592)
                self.assertEqual(CentralProgramSetting.objects.filter(source_id__isnull=False).count(), 31)
                self.assertEqual(old.settings.filter(kind='prompting').count(), 1)
                self.assertEqual(old_target.prompting_levels, [])
                self.assertEqual(old_target.settings.count(), 0)
                transaction.set_rollback(True)
