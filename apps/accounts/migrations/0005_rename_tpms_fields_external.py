from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_user_organization'),
    ]

    operations = [
        migrations.RenameField(
            model_name='user',
            old_name='tpms_admin_id',
            new_name='external_admin_id',
        ),
        migrations.RenameField(
            model_name='user',
            old_name='tpms_employee_id',
            new_name='external_employee_id',
        ),
    ]
