import json
from django.db import migrations


def link_existing(apps, schema_editor):
    Setting = apps.get_model('central_library', 'CentralProgramSetting')
    Program = apps.get_model('central_library', 'CentralProgram')
    Target = apps.get_model('central_library', 'CentralTarget')
    prompt_cache = {}

    def prompting(source):
        if not source.prompting_levels:
            return
        levels = [dict(level) for level in source.prompting_levels]
        if not any(level.get('is_success') for level in levels):
            best = max(range(len(levels)), key=lambda i: levels[i].get('score', 0))
            levels[best]['is_success'] = True
        for level in levels:
            level.setdefault('weight', level.get('score', int(bool(level.get('is_success')))))
            level['score'] = level['weight']
            level.setdefault('is_success', False)
            level.setdefault('exclude_from_fading', False)
        configuration = dict(description='', levels=levels, outcome_measurement='binary',
                             fading_hint_mode='none', fading_hint_settings={})
        signature = json.dumps(configuration, sort_keys=True)
        if signature not in prompt_cache:
            setting, _ = Setting.objects.get_or_create(
                kind='prompting', name=f'Imported prompting {len(prompt_cache) + 1}',
                defaults={'configuration': configuration},
            )
            prompt_cache[signature] = setting
        source.settings.add(prompt_cache[signature])

    for program in Program.objects.all().iterator():
        if program.treatment_area:
            area, _ = Setting.objects.get_or_create(kind='treatment_area', name=program.treatment_area,
                defaults={'configuration': {'description': '', 'is_active': True}})
            program.settings.add(area)
        for name in program.tags:
            tag, _ = Setting.objects.get_or_create(kind='tag', name=name,
                defaults={'configuration': {'color': '#6366f1', 'is_active': True}})
            program.settings.add(tag)
        prompting(program)
    for target in Target.objects.all().iterator():
        prompting(target)


class Migration(migrations.Migration):
    dependencies = [('central_library', '0013_centralprogram_configuration_and_more')]
    operations = [migrations.RunPython(link_existing, migrations.RunPython.noop)]
