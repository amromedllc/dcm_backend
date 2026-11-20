from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0022_alter_user_role_add_sales'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='superadmin_delegated_permissions',
            field=models.JSONField(blank=True, db_default=dict, default=dict),
        ),
    ]
