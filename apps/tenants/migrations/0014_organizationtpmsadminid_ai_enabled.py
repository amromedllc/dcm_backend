from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0013_facility_last_appointment_sync'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationtpmsadminid',
            name='ai_enabled',
            field=models.BooleanField(default=False),
        ),
    ]
