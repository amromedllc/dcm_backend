from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0038_promptingtemplate_is_locked'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='program',
            name='fading_template',
        ),
        migrations.RemoveField(
            model_name='target',
            name='fading_template',
        ),
        migrations.RemoveField(
            model_name='target',
            name='fading_mode',
        ),
        migrations.DeleteModel(
            name='FadingTemplate',
        ),
    ]
