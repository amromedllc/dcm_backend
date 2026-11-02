from django.db import migrations, models


def preserve_push_settings(apps, schema_editor):
    alias = schema_editor.connection.alias
    for name in ('NotificationPreference', 'RoleNotificationPolicy'):
        model = apps.get_model('notifications', name)
        model.objects.using(alias).update(mobile_enabled=models.F('web_enabled'))
    tokens = apps.get_model('notifications', 'FirebaseMessagingToken')
    tokens.objects.using(alias).filter(user_agent__icontains='android').update(platform='android')


class Migration(migrations.Migration):
    dependencies = [('notifications', '0011_appointment_notification_events')]
    operations = [
        migrations.AddField(model_name='notificationpreference', name='mobile_enabled', field=models.BooleanField(default=True)),
        migrations.AddField(model_name='rolenotificationpolicy', name='mobile_enabled', field=models.BooleanField(default=True)),
        migrations.AddField(model_name='firebasemessagingtoken', name='platform', field=models.CharField(max_length=10, choices=[('web', 'Web'), ('android', 'Android')], default='web')),
        migrations.RunPython(preserve_push_settings, migrations.RunPython.noop),
    ]
