from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0002_pulljob'),
    ]

    operations = [
        migrations.AddField(
            model_name='pulljob',
            name='cancelled_count',
            field=models.IntegerField(default=0),
        ),
    ]
