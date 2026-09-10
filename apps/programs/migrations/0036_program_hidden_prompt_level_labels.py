from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0035_program_phase_statuses'),
    ]

    operations = [
        migrations.AddField(
            model_name='program',
            name='hidden_prompt_level_labels',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
