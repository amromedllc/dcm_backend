from django.db import migrations, models


def reassign_reports_to_notes(apps, schema_editor):
    NoteTemplate = apps.get_model('notes', 'NoteTemplate')
    NoteTemplate.objects.filter(template_type='reports').update(template_type='notes')


class Migration(migrations.Migration):

    dependencies = [
        ('notes', '0007_notetemplate_body_template_notetemplate_lock_content_and_more'),
    ]

    operations = [
        migrations.RunPython(reassign_reports_to_notes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='notetemplate',
            name='template_type',
            field=models.CharField(choices=[('notes', 'Notes'), ('forms', 'Forms')], default='notes', max_length=20),
        ),
    ]
