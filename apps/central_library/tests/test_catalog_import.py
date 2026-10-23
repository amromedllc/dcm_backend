import copy
import json
from pathlib import Path

from django.test import SimpleTestCase

from apps.central_library.catalog_import import build_catalog
from apps.programs.api import _resolve_measurement_fields
from apps.programs.schemas import PromptingTemplateCreateRequest


CATALOG = Path(__file__).resolve().parent.parent / 'data' / 'progressly_master_catalog.json'


class CatalogImportTests(SimpleTestCase):
    def setUp(self):
        self.document = json.loads(CATALOG.read_text())

    def test_complete_workbook_and_domain_counts(self):
        catalog = build_catalog(self.document)
        self.assertEqual(len(catalog), 163)
        self.assertEqual(sum(len(p['targets']) for p in catalog), 489)
        self.assertEqual(len({p['domain'] for p in catalog}), 13)
        self.assertTrue(all(len(p['targets']) == 3 for p in catalog))
        self.assertTrue(all(p['fields']['phase'] == 'on_hold' for p in catalog))

    def test_duplicate_titles_remain_separate_source_programs(self):
        matches = [p for p in build_catalog(self.document) if p['fields']['name'] == 'Completes assigned work']
        self.assertEqual(len(matches), 2)
        self.assertNotEqual(matches[0]['source_id'], matches[1]['source_id'])

    def test_all_targets_can_pass_existing_clone_measurement_resolution(self):
        for program in build_catalog(self.document):
            for target in program['targets']:
                fields = target['fields']
                _resolve_measurement_fields(fields['measurement_type'], fields['measurement'], fields['timer_type'])
                if fields['prompting_levels']:
                    template = PromptingTemplateCreateRequest(name='Example', levels=fields['prompting_levels'])
                    self.assertEqual(sum(level.is_success for level in template.levels), 1)

    def test_frequency_is_count_not_percentage(self):
        matches = [t for p in build_catalog(self.document) for t in p['targets'] if t['fields']['measurement'] == 'frequency']
        self.assertEqual(len(matches), 57)
        self.assertTrue(all(t['fields']['measurement_type'] == 'discrete_trial' for t in matches))

    def test_unsupported_or_unconfigured_modes_preserve_the_source(self):
        catalog = build_catalog(self.document)
        targets = [t for p in catalog for t in p['targets'] if t['fields']['measurement_type'] == 'instructions']
        self.assertEqual(len(targets), 114)
        for target in targets:
            self.assertEqual(target['fields']['teaching_instructions'], '')
            self.assertTrue(target['source_id'])
            self.assertEqual(target['fields']['prompting_levels'], [])
        self.assertEqual(sum('Requires configuration' in p['fields']['tags'] for p in catalog), 38)

    def test_metadata_and_generated_warnings_are_not_instructions(self):
        catalog = build_catalog(self.document)
        for program in catalog:
            self.assertEqual(program['fields']['instructions'], '')
            for target in program['targets']:
                self.assertEqual(target['fields']['teaching_instructions'], '')

    def test_only_actual_teaching_content_is_imported(self):
        row = self.document['sheets']['Programs'][0]
        row['prompting_procedure'] = 'Model the request.'
        row['error_correction'] = 'Repeat the model.'
        row['clinical_notes'] = 'Metadata should not become instructions.'
        target = self.document['sheets']['Targets'][0]
        target['instruction_sd'] = 'What do you want?'
        target['teaching_instructions'] = 'Wait for the response.'
        catalog = build_catalog(self.document)
        self.assertEqual(catalog[0]['fields']['instructions'],
                         'Prompting procedure: Model the request.\n\nError correction: Repeat the model.')
        fields = catalog[0]['targets'][0]['fields']
        self.assertEqual(fields['sd_text'], 'What do you want?')
        self.assertEqual(fields['teaching_instructions'], 'Wait for the response.')

    def test_duplicate_ids_or_missing_parents_are_rejected(self):
        for sheet, key in [('Programs', 'program_id'), ('Targets', 'target_id')]:
            document = copy.deepcopy(self.document)
            document['sheets'][sheet][1][key] = document['sheets'][sheet][0][key]
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                build_catalog(document)
        self.document['sheets']['Targets'][0]['program_id'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'unknown program_id'):
            build_catalog(self.document)

    def test_unknown_measurements_categories_and_statuses_are_rejected(self):
        for key in ['default_measurement', 'program_type', 'status']:
            document = copy.deepcopy(self.document)
            document['sheets']['Programs'][0][key] = 'unknown'
            with self.assertRaises(ValueError):
                build_catalog(document)

    def test_missing_targets_and_parent_mismatches_are_rejected(self):
        self.document['sheets']['Targets'] = self.document['sheets']['Targets'][3:]
        with self.assertRaisesRegex(ValueError, 'has no targets'):
            build_catalog(self.document)
        self.document = json.loads(CATALOG.read_text())
        self.document['sheets']['Targets'][0]['clinical_domain'] = 'wrong domain'
        with self.assertRaisesRegex(ValueError, 'does not match parent'):
            build_catalog(self.document)

    def test_source_is_not_mutated(self):
        before = copy.deepcopy(self.document)
        build_catalog(self.document)
        self.assertEqual(self.document, before)


class CorrectedCatalogTests(SimpleTestCase):
    def setUp(self):
        self.document = json.loads((CATALOG.parent / 'progressly_corrected_catalog.json').read_text())

    def test_complete_corrected_catalog_links_and_source_ids(self):
        from apps.central_library.catalog_import import build_settings
        catalog = build_catalog(self.document)
        settings = build_settings(self.document)
        original = build_catalog(json.loads(CATALOG.read_text()))
        self.assertEqual([p['source_id'] for p in catalog], [p['source_id'] for p in original])
        self.assertEqual([t['source_id'] for p in catalog for t in p['targets']], [t['source_id'] for p in original for t in p['targets']])
        self.assertEqual(len(settings), 31)
        self.assertEqual(sum(len(p['setting_refs']) for p in catalog), 592)
        self.assertEqual(sum(len(t['setting_refs']) for p in catalog for t in p['targets']), 0)
        for program in catalog:
            for target in program['targets']:
                if any(settings[ref]['kind'] == 'prompting' for ref in program['setting_refs']):
                    self.assertEqual(target['fields']['prompting_levels'], [])

    def test_duplicate_or_unknown_link_rejected_before_writes(self):
        for invalid in [dict(self.document['sheets']['Program Setting Links'][0]), {'program_id': 'missing', 'setting_id': 'SET-0001'}]:
            doc = copy.deepcopy(self.document)
            doc['sheets']['Program Setting Links'].append(invalid)
            with self.assertRaises(ValueError): build_catalog(doc)

    def test_unreviewed_and_singular_conflicts_are_rejected(self):
        doc = copy.deepcopy(self.document)
        doc['sheets']['Central Settings'][2]['review_status'] = 'needs_clinician_review'
        with self.assertRaises(ValueError): build_catalog(doc)
        doc = copy.deepcopy(self.document)
        doc['sheets']['Program Setting Links'].append({'program_id': 'PRG-0001', 'setting_id': 'SET-0001'})
        with self.assertRaises(ValueError): build_catalog(doc)

    def test_invalid_json_and_facility_ids_are_rejected(self):
        for value in ['{invalid', '{"workflow_template_id":123}']:
            doc = copy.deepcopy(self.document)
            doc['sheets']['Targets'][0]['configuration_json'] = value
            with self.assertRaises(ValueError): build_catalog(doc)

    def test_target_links_cannot_reference_tags(self):
        self.document['sheets']['Target Setting Links'].append({'target_id': 'PRG-0001-T01', 'setting_id': 'SET-0014'})
        with self.assertRaises(ValueError): build_catalog(self.document)

    def test_unknown_dependency_or_missing_sheet_rejected(self):
        doc = copy.deepcopy(self.document)
        del doc['sheets']['Target Setting Links']
        with self.assertRaises(ValueError): build_catalog(doc)
        self.document['sheets']['Central Settings'][0]['dependency_setting_ids'] = 'missing'
        with self.assertRaises(ValueError): build_catalog(self.document)
