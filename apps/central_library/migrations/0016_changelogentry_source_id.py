from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('central_library', '0015_centralprogram_source_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='changelogentry',
            name='source_id',
            field=models.CharField(blank=True, max_length=80, null=True, unique=True),
        ),
    ]
