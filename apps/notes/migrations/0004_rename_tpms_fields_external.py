from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('notes', '0003_noteassignment'),
    ]

    operations = [
        migrations.RenameField(
            model_name='lessonnote',
            old_name='tpms_client_id',
            new_name='external_client_id',
        ),
        migrations.RenameField(
            model_name='noteassignment',
            old_name='tpms_appointment_id',
            new_name='external_appointment_id',
        ),
        migrations.RenameField(
            model_name='noteassignment',
            old_name='tpms_client_id',
            new_name='external_client_id',
        ),
        migrations.AlterUniqueTogether(
            name='noteassignment',
            unique_together={('external_appointment_id', 'template')},
        ),
    ]
