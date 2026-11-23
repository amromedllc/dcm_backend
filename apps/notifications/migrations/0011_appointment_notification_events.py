from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0010_notificationpreference_locked'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('session_submitted', 'Session submitted for review'),
                    ('session_approved', 'Session approved'),
                    ('session_rejected', 'Session rejected'),
                    ('target_advanced', 'Target automatically advanced'),
                    ('target_prompt_level_changed', 'Target prompt level automatically changed'),
                    ('report_review_request', 'Report review request'),
                    ('appointment_reminder', 'Appointment reminder'),
                    ('appointment_changed', 'Appointment changed'),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
