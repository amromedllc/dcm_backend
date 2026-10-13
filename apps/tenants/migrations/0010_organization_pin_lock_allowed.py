from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0009_organization_integration_credentials'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='pin_lock_allowed',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='organization',
            name='automatic_logout_minutes',
            field=models.PositiveIntegerField(
                choices=[
                    (2, '2 minutes'),
                    (5, '5 minutes'),
                    (15, '15 minutes'),
                    (30, '30 minutes'),
                    (540, '9 hours'),
                    (1440, '24 hours'),
                    (10080, '1 week'),
                ],
                default=540,
            ),
        ),
    ]
