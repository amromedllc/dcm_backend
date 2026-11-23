# Generated manually to mirror makemigrations output (no local Django env available)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notes', '0011_lessonnote_old_form_opened_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='lessonnote',
            name='old_form_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='lessonnote',
            name='old_form_url',
            field=models.TextField(blank=True),
        ),
    ]
