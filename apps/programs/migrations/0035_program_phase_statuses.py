from django.db import migrations, models


def migrate_program_phases(apps, schema_editor):
    Program = apps.get_model('programs', 'Program')
    Program.objects.filter(status='inactive').update(phase='hold', status='active')
    phase_map = {
        'teaching': 'active',
        'generalizing': 'active',
        'mastered': 'maintenance',
        'on_hold': 'hold',
    }
    for old, new in phase_map.items():
        Program.objects.filter(phase=old).update(phase=new)


def reverse_program_phases(apps, schema_editor):
    Program = apps.get_model('programs', 'Program')
    reverse_map = {
        'active': 'teaching',
        'hold': 'on_hold',
        'closed': 'mastered',
        'discontinued': 'on_hold',
        'waiting': 'teaching',
    }
    for old, new in reverse_map.items():
        Program.objects.filter(phase=old).update(phase=new)


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0034_target_interval_pause_on_warning_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_program_phases, reverse_program_phases),
        migrations.AlterField(
            model_name='program',
            name='phase',
            field=models.CharField(
                blank=True,
                choices=[
                    ('waiting', 'Waiting'),
                    ('baseline', 'Baseline'),
                    ('active', 'Active'),
                    ('maintenance', 'Maintenance'),
                    ('hold', 'Hold'),
                    ('closed', 'Closed'),
                    ('discontinued', 'Discontinued'),
                ],
                default='active',
                max_length=20,
            ),
        ),
    ]
