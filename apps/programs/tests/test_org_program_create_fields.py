from django.test import SimpleTestCase

from apps.programs.schemas import OrgProgramCreateRequest


class OrgProgramCreateFieldsTests(SimpleTestCase):
    def test_accepts_prompt_level_and_baseline_settings(self):
        data = OrgProgramCreateRequest(
            name='Mand Training', hidden_prompt_level_labels=['Full Physical'], baseline_notes='Starts at 20%',
        )
        self.assertEqual(data.hidden_prompt_level_labels, ['Full Physical'])
        self.assertEqual(data.baseline_notes, 'Starts at 20%')

    def test_defaults_are_empty(self):
        data = OrgProgramCreateRequest(name='X')
        self.assertEqual(data.hidden_prompt_level_labels, [])
        self.assertEqual(data.baseline_notes, '')
