from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tenants', '0011_shared_domains')]

    operations = [
        migrations.AddField(
            model_name='organizationtpmsadminid',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
    ]
