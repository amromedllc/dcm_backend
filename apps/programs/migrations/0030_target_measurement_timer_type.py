from django.db import migrations, models


# measurement_type value -> measurement value for existing rows.
_BACKFILL = {
    'discrete_trial': 'percent_correct',
    'duration': 'total_observed_duration',
    'rate': 'rate_per_minute',
}


def backfill(apps, schema_editor):
    Target = apps.get_model('programs', 'Target')
    for mt, measurement in _BACKFILL.items():
        Target.objects.filter(measurement_type=mt, measurement='').update(measurement=measurement)
    Target.objects.filter(measurement='').update(measurement='percent_correct')
    Target.objects.filter(measurement_type='rate', timer_type='').update(timer_type='count_up')


def unbackfill(apps, schema_editor):
    Target = apps.get_model('programs', 'Target')
    Target.objects.update(measurement='', timer_type='')


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0029_savedtableview'),
    ]

    operations = [
        migrations.AddField(
            model_name='target',
            name='measurement',
            field=models.CharField(
                blank=True,
                default='',
                max_length=32,
                choices=[
                    ('percent_correct', 'Percent Correct'),
                    ('frequency', 'Frequency'),
                    ('rate_per_hour', 'Rate per Hour'),
                    ('rate_per_minute', 'Rate per Minute'),
                    ('total_observed_duration', 'Total Observed Duration'),
                    ('min_observed_duration', 'Min. Observed Duration'),
                    ('max_observed_duration', 'Max. Observed Duration'),
                    ('avg_observed_duration', 'Avg. Observed Duration'),
                ],
            ),
        ),
        migrations.AddField(
            model_name='target',
            name='timer_type',
            field=models.CharField(
                blank=True,
                default='',
                max_length=20,
                choices=[
                    ('count_up', 'Count Up (stopwatch)'),
                    ('countdown', 'Countdown (egg-timer)'),
                    ('session_timer', 'Link to Session Timer'),
                ],
            ),
        ),
        migrations.RunPython(backfill, unbackfill),
    ]
