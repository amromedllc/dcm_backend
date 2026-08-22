from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import apps.programs.models


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0027_target_subitem_progression'),
        ('tenants', '0003_remove_organization_tpms_admin_id_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='program',
            name='prompting_template',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='programs',
                to='programs.promptingtemplate',
            ),
        ),
        migrations.CreateModel(
            name='ProgramMaterial',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=255)),
                ('material_type', models.CharField(choices=[('image', 'Image'), ('video', 'Video'), ('document', 'Document')], max_length=20)),
                ('file', models.FileField(max_length=500, upload_to=apps.programs.models._program_material_upload_path)),
                ('content_type', models.CharField(blank=True, max_length=120)),
                ('file_size', models.PositiveIntegerField(default=0)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='tenants.organization')),
                ('program', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='materials', to='programs.program')),
            ],
            options={
                'app_label': 'programs',
                'ordering': ['-created_at'],
            },
        ),
    ]
