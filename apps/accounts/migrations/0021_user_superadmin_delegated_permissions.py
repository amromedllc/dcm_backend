from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0020_impersonationlog'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='superadmin_delegated_permissions',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
