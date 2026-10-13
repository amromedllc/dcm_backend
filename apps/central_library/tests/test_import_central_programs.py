from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from apps.central_library.models import CentralProgram, CentralProgramFolder


class ImportCentralProgramsCommandTests(TestCase):
    def test_imports_csv_program_prompt_levels(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'central_programs.csv'
            path.write_text(
                '\n'.join([
                    'program_id,program_name,clinical_domain,program_type,default_measurement,alternative_measurements,response_options,prompt_levels,measurement_unit,setting_options',
                    'P-001,Requesting Help,Communication,Skill Acquisition,percent_correct,,Correct|Incorrect,Independent|Gestural|Full Physical,%,',
                ]),
                encoding='utf-8',
            )

            out = StringIO()
            call_command('import_central_programs', str(path), '--folder', 'Imported Programs', stdout=out)

        folder = CentralProgramFolder.objects.get(name='Imported Programs')
        program = CentralProgram.objects.get(folder=folder, name='Requesting Help')
        self.assertEqual(program.category, CentralProgram.Category.SKILL_ACQUISITION)
        self.assertEqual(program.treatment_area, 'Communication')
        self.assertEqual(program.prompting_levels[0]['label'], 'Independent')
        self.assertTrue(program.prompting_levels[0]['is_success'])
        self.assertIn('default_measurement: percent_correct', program.instructions)
        self.assertIn('1 created', out.getvalue())

    def test_replace_updates_existing_program_by_name(self):
        folder = CentralProgramFolder.objects.create(name='Imported Programs')
        CentralProgram.objects.create(
            folder=folder,
            name='Requesting Help',
            treatment_area='Old',
            prompting_levels=[],
        )

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'central_programs.csv'
            path.write_text(
                '\n'.join([
                    'program_name,clinical_domain,program_type,prompt_levels',
                    'Requesting Help,Communication,Assessment,"[{""label"": ""Independent"", ""score"": 1, ""is_success"": true}]"',
                ]),
                encoding='utf-8',
            )

            call_command('import_central_programs', str(path), '--folder', folder.name, '--replace')

        program = CentralProgram.objects.get(folder=folder, name='Requesting Help')
        self.assertEqual(program.category, CentralProgram.Category.ASSESSMENT)
        self.assertEqual(program.treatment_area, 'Communication')
        self.assertEqual(program.prompting_levels, [
            {'label': 'Independent', 'score': 1, 'abbreviation': 'IND', 'is_success': True},
        ])
