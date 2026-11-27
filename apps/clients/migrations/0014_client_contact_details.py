from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('clients', '0013_baseline_client_status_observations')]

    operations = [
        migrations.AddField(
            model_name='client',
            name='middle_name',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='client',
            name='gender',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
        migrations.AddField(
            model_name='client',
            name='email',
            field=models.CharField(blank=True, default='', max_length=254),
        ),
        migrations.AddField(
            model_name='client',
            name='email_type',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
        migrations.AddField(
            model_name='client',
            name='phone_number',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
    ]
