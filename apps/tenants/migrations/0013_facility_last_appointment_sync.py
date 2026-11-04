from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0012_facility_integration_credentials'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationtpmsadminid',
            name='last_appointment_sync_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
