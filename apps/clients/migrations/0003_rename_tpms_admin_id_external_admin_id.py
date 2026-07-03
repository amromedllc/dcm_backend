from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0002_client_tpms_admin_id'),
    ]

    operations = [
        migrations.RenameField(
            model_name='client',
            old_name='tpms_admin_id',
            new_name='external_admin_id',
        ),
    ]
