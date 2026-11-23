from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dcm_sessions', '0016_sessionsupervisionnote'),
    ]

    operations = [
        migrations.AddField(
            model_name='appointment',
            name='reminder_sent_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
