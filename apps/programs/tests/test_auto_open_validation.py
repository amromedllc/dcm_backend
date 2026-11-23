import unittest

from apps.programs.services import validate_auto_open_config


class AutoOpenValidationTests(unittest.TestCase):
    def test_defaults_and_ui_configurations(self):
        validate_auto_open_config({})
        for direction in ('first', 'last', 'all'):
            for status in ('probe', 'acquisition'):
                validate_auto_open_config({
                    'auto_open_waiting': True, 'auto_open_all': False,
                    'auto_open_status': status, 'auto_open_direction': direction,
                    'auto_open_max_targets': 2,
                })

    def test_invalid_values_are_rejected_including_disabled_configs(self):
        cases = {
            'auto_open_waiting': ['true', 1, None],
            'auto_open_all': ['false', 0, None],
            'auto_open_status': ['waiting', 'mastered', 'closed', 'unknown', None],
            'auto_open_direction': ['unknown', '', None],
            'auto_open_max_targets': [0, -1, True, 1.5, '2', None],
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_auto_open_config({key: value})
