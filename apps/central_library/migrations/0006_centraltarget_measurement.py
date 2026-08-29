from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('central_library', '0005_knowledgebaseimport'),
    ]

    operations = [
        migrations.AddField(
            model_name='centraltarget',
            name='measurement',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='centraltarget',
            name='timer_type',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
    ]
