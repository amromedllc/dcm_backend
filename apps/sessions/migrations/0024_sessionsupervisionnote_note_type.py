from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('dcm_sessions', '0023_behaviorevent_observation_ended_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='sessionsupervisionnote',
            name='note_type',
            field=models.CharField(
                max_length=24,
                choices=[('guidance', 'Supervisor guidance'), ('program_modification', 'Program modification')],
                default='guidance',
            ),
        ),
    ]
