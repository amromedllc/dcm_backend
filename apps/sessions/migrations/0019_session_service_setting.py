from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('dcm_sessions', '0018_trialevent_client_event_id_and_more')]

    operations = [
        migrations.AddField(
            model_name=model_name,
            name='service_setting',
            field=models.CharField(
                max_length=20,
                choices=[(value, value) for value in ('Home', 'Office', 'School', 'Community', 'Other', 'Telehealth')],
                blank=True,
                default='',
            ),
        )
        for model_name in ('sessionrun', 'trialevent')
    ]
