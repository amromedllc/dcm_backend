from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('dcm_sessions', '0019_session_service_setting')]

    operations = [
        migrations.AddField(
            model_name='sessionrun',
            name='service_address',
            field=models.JSONField(default=dict, blank=True),
        ),
    ]
