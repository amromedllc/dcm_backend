from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0015_target_status_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='targetstatus',
            name='is_active',
            field=models.BooleanField(default=True, help_text='Inactive statuses are hidden everywhere except Settings'),
        ),
    ]
