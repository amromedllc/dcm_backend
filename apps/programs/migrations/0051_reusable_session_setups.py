from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programs', '0050_alter_target_measurement_type')]
    operations = [
        migrations.AddField(model_name='lesson', name='is_reusable', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='lesson', name='is_default', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='lessonprogram', name='is_pinned', field=models.BooleanField(default=False)),
        migrations.AddConstraint(model_name='lesson', constraint=models.UniqueConstraint(fields=('organization', 'external_client_id'), condition=models.Q(is_default=True), name='unique_default_session_setup_per_client')),
        migrations.AddConstraint(model_name='lesson', constraint=models.CheckConstraint(condition=models.Q(is_default=False) | models.Q(is_reusable=True, is_active=True), name='default_session_setup_active_reusable')),
    ]
