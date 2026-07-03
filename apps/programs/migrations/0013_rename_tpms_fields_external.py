from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0012_update_measurement_types'),
    ]

    operations = [
        migrations.RenameField(
            model_name='program',
            old_name='tpms_client_id',
            new_name='external_client_id',
        ),
        migrations.RenameField(
            model_name='lesson',
            old_name='tpms_client_id',
            new_name='external_client_id',
        ),
    ]
