from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('central_library', '0008_knowledgebasetopic_video'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='centralprogram',
            index=models.Index(fields=['status', 'display_order', 'name'], name='central_prog_status_ord_idx'),
        ),
        migrations.AddIndex(
            model_name='centralprogram',
            index=models.Index(fields=['folder', 'display_order'], name='central_prog_folder_ord_idx'),
        ),
        migrations.AddIndex(
            model_name='centraltarget',
            index=models.Index(fields=['program', 'display_order'], name='central_target_prog_ord_idx'),
        ),
        migrations.AddIndex(
            model_name='knowledgebasemodule',
            index=models.Index(fields=['is_active', 'display_order', 'title'], name='kb_module_active_ord_idx'),
        ),
        migrations.AddIndex(
            model_name='knowledgebasetopic',
            index=models.Index(fields=['module', 'is_active', 'display_order'], name='kb_topic_module_active_idx'),
        ),
    ]
