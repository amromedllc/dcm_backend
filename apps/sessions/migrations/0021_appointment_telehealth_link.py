from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('dcm_sessions', '0020_sessionrun_service_address')]
    operations = [
        migrations.AddField(
            model_name='appointment', name='telehealth_link',
            field=models.URLField(max_length=2048, blank=True, default=''),
        ),
    ]
