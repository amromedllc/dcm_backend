from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0010_add_target_status_change'),
    ]

    operations = [
        migrations.AddField(
            model_name='target',
            name='mastery_mode',
            field=models.CharField(
                choices=[('manual', 'Manual'), ('automatic', 'Automatic')],
                default='manual',
                max_length=10,
            ),
        ),
    ]
