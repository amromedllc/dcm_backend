"""AI-drafted note templates: only shapes DCM accepts survive."""
from unittest import mock

from django.test import SimpleTestCase

from apps.notes.template_draft import generate_template_draft, normalize_template_draft
from shared.ai_client import AIError


class NormalizeTemplateDraftTests(SimpleTestCase):
    def test_bad_types_and_duplicate_keys_are_cleaned(self):
        draft = normalize_template_draft({
            'name': '<b>Mand training</b> note',
            'fields': [
                {'label': 'Summary', 'type': 'weird'},
                {'label': 'Summary', 'key': 'summary', 'type': 'text'},
                {'label': 'Mood', 'type': 'select', 'options': ['Calm']},
                {'label': 'Prompt used', 'type': 'select', 'options': ['Full', 'Partial', 'Full']},
            ],
        })
        self.assertEqual(draft['name'], 'Mand training note')
        types = {f['key']: f['type'] for f in draft['fields']}
        self.assertEqual(types['summary'], 'textarea')
        self.assertEqual(types['mood'], 'text')  # a select with < 2 options falls back to text
        self.assertEqual(next(f for f in draft['fields'] if f['key'] == 'prompt_used')['options'], ['Full', 'Partial'])
        self.assertEqual(len(draft['fields']), 3)

    def test_empty_fields_raise(self):
        with self.assertRaises(AIError):
            normalize_template_draft({'fields': []})

    def test_prompt_only_contains_program_info(self):
        program = mock.MagicMock()
        program.name = 'Mand training'
        program.category = 'skill_acquisition'
        program.objective = 'Request items'
        program.targets.all.return_value = [mock.MagicMock(name='x')]
        with mock.patch('apps.notes.template_draft.chat_json', return_value={'fields': [{'label': 'Summary'}]}) as chat:
            generate_template_draft(program, 'keep it short')
        user_prompt = chat.call_args[0][1]
        self.assertIn('Mand training', user_prompt)
        self.assertIn('keep it short', user_prompt)
