from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0011_add_target_mastery_mode'),
    ]

    operations = [
        migrations.AlterField(
            model_name='target',
            name='measurement_type',
            field=models.CharField(
                choices=[
                    ('discrete_trial',  'Discrete Trial'),
                    ('duration',        'Duration'),
                    ('rate',            'Rate'),
                    ('task_analysis',   'Task Analysis'),
                    ('set_of_targets',  'Set of Targets'),
                    ('shaping',         'Shaping'),
                    ('instructions',    'Instructions'),
                    ('trial_by_trial',  'Trial by Trial (legacy)'),
                    ('frequency',       'Frequency (legacy)'),
                    ('whole_interval',  'Whole Interval (legacy)'),
                    ('partial_interval', 'Partial Interval (legacy)'),
                ],
                default='discrete_trial',
                max_length=30,
            ),
        ),
        # Migrate existing trial_by_trial rows to discrete_trial
        migrations.RunSQL(
            "UPDATE programs_target SET measurement_type = 'discrete_trial' WHERE measurement_type = 'trial_by_trial';",
            reverse_sql="UPDATE programs_target SET measurement_type = 'trial_by_trial' WHERE measurement_type = 'discrete_trial';",
        ),
    ]
