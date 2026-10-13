from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_passwordsetlink'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='pin_hash',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='user',
            name='pin_lock_enabled',
            field=models.BooleanField(default=False),
        ),
    ]
