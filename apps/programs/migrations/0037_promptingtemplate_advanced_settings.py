from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0036_program_hidden_prompt_level_labels'),
    ]

    operations = [
        migrations.AddField(
            model_name='promptingtemplate',
            name='outcome_measurement',
            field=models.CharField(choices=[('binary', 'Successful / Unsuccessful'), ('rating_scale', 'Rating Scale'), ('weighted', 'Weighted')], default='binary', max_length=20),
        ),
        migrations.AddField(
            model_name='promptingtemplate',
            name='fading_hint_mode',
            field=models.CharField(choices=[('none', 'None'), ('across_trials', 'Most-to-Least (across trials)'), ('across_sessions', 'Most-to-Least (across sessions)')], default='none', max_length=20),
        ),
        migrations.AddField(
            model_name='promptingtemplate',
            name='fading_hint_settings',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
