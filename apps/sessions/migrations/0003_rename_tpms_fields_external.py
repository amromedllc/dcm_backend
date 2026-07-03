from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('dcm_sessions', '0002_remove_appointment_client_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='appointment',
            old_name='tpms_client_id',
            new_name='external_client_id',
        ),
        migrations.RenameField(
            model_name='sessionrun',
            old_name='tpms_client_id',
            new_name='external_client_id',
        ),
        migrations.RenameField(
            model_name='sessionrun',
            old_name='tpms_appointment_id',
            new_name='external_appointment_id',
        ),
    ]
