from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_user_avatar'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='signature_data',
            field=models.TextField(blank=True, default=''),
        ),
    ]
