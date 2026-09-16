from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dcm_sessions', '0010_standalone_abc_categories'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='appointment',
            index=models.Index(fields=['organization', 'external_client_id', 'start_time'], name='appt_org_client_start_idx'),
        ),
        migrations.AddIndex(
            model_name='appointment',
            index=models.Index(fields=['organization', 'staff', 'start_time'], name='appt_org_staff_start_idx'),
        ),
        migrations.AddIndex(
            model_name='appointment',
            index=models.Index(fields=['organization', 'status', 'start_time'], name='appt_org_status_start_idx'),
        ),
        migrations.AddIndex(
            model_name='sessionrun',
            index=models.Index(fields=['organization', 'external_client_id', 'started_at'], name='sess_org_client_start_idx'),
        ),
        migrations.AddIndex(
            model_name='sessionrun',
            index=models.Index(fields=['organization', 'status', 'started_at'], name='sess_org_status_start_idx'),
        ),
        migrations.AddIndex(
            model_name='sessionrun',
            index=models.Index(fields=['organization', 'external_appointment_id'], name='sess_org_appt_idx'),
        ),
        migrations.AddIndex(
            model_name='trialevent',
            index=models.Index(fields=['organization', 'target_id', 'recorded_at'], name='trial_org_target_time_idx'),
        ),
        migrations.AddIndex(
            model_name='trialevent',
            index=models.Index(fields=['session_run', 'target_id'], name='trial_session_target_idx'),
        ),
        migrations.AddIndex(
            model_name='behaviorevent',
            index=models.Index(fields=['organization', 'target_id', 'occurred_at'], name='behavior_org_target_time_idx'),
        ),
        migrations.AddIndex(
            model_name='abcevent',
            index=models.Index(fields=['organization', 'external_client_id', 'occurred_at'], name='abc_org_client_time_idx'),
        ),
    ]
