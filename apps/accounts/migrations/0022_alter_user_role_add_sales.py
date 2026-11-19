from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0021_user_superadmin_delegated_permissions'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('admin', 'Admin'),
                    ('supervisor', 'Clinical Supervisor'),
                    ('staff', 'RBT / Staff'),
                    ('caregiver', 'Caregiver'),
                    ('reporting', 'Reporting / Audit'),
                    ('sales', 'Sales'),
                ],
                default='staff',
                max_length=20,
            ),
        ),
    ]
