from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0009_report_review_request_event'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='locked',
            field=models.BooleanField(blank=True, default=None, null=True),
        ),
    ]
