from types import SimpleNamespace
from unittest import mock
from django.test import SimpleTestCase
from apps.central_library import settings_service


class ImportedSettingNameTests(SimpleTestCase):
    def test_generated_prompt_name_uses_readable_title_and_preserves_collisions(self):
        for collisions, expected in (([False], 'Requests preferred food'),
                                     ([True, False], 'Requests preferred food (2)')):
            source = SimpleNamespace(name='Requests preferred food', prompting_levels=[{'label': 'Independent'}], settings=mock.Mock())
            source.settings.values_list.return_value = []
            queryset = mock.Mock()
            queryset.first.return_value = None
            queryset.exists.side_effect = collisions
            with mock.patch.object(settings_service, 'normalize_configuration', return_value={}), \
                 mock.patch.object(settings_service.CentralProgramSetting.objects, 'filter', return_value=queryset), \
                 mock.patch.object(settings_service, 'legacy_setting') as create:
                settings_service.sync_legacy_links(source)
                self.assertEqual(create.call_args.args, ('prompting', expected))
                source.settings.add.assert_called_once_with(create.return_value)
