from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0037_promptingtemplate_advanced_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='promptingtemplate',
            name='is_locked',
            field=models.BooleanField(default=False),
        ),
    ]
