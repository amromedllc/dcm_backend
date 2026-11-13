from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0015_facility_form_builder_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationtpmsadminid',
            name='last_client_sync_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='organizationtpmsadminid',
            name='last_provider_sync_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
