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
            self.assertIn('Requires configuration:', target['fields']['teaching_instructions'])
            self.assertIn(target['source_id'], target['fields']['teaching_instructions'])
            self.assertEqual(target['fields']['prompting_levels'], [])
        self.assertEqual(sum('Requires configuration' in p['fields']['tags'] for p in catalog), 38)

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
