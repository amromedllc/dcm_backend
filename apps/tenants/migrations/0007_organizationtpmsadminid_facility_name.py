from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0006_organizationtpmsadminid_email_notifications_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationtpmsadminid',
            name='facility_name',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
