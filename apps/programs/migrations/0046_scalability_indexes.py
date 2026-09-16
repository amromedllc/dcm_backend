from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0045_programdatafield_show_in_client_sessions'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='program',
            index=models.Index(fields=['organization', 'external_client_id', 'status', 'display_order'], name='program_org_client_status_idx'),
        ),
        migrations.AddIndex(
            model_name='program',
            index=models.Index(fields=['organization', 'is_template', 'display_order'], name='program_org_template_ord_idx'),
        ),
        migrations.AddIndex(
            model_name='program',
            index=models.Index(fields=['organization', 'folder', 'display_order'], name='program_org_folder_ord_idx'),
        ),
        migrations.AddIndex(
            model_name='target',
            index=models.Index(fields=['organization', 'program', 'status', 'display_order'], name='target_org_program_status_idx'),
        ),
        migrations.AddIndex(
            model_name='target',
            index=models.Index(fields=['organization', 'program', 'module', 'display_order'], name='target_org_program_module_idx'),
        ),
        migrations.AddIndex(
            model_name='target',
            index=models.Index(fields=['organization', 'program', 'submodule', 'display_order'], name='target_org_program_submod_idx'),
        ),
        migrations.AddIndex(
            model_name='targetsubitem',
            index=models.Index(fields=['organization', 'target', 'status', 'display_order'], name='subitem_org_target_status_idx'),
        ),
        migrations.AddIndex(
            model_name='programmodule',
            index=models.Index(fields=['organization', 'program', 'display_order'], name='module_org_program_ord_idx'),
        ),
        migrations.AddIndex(
            model_name='programsubmodule',
            index=models.Index(fields=['organization', 'module', 'display_order'], name='submodule_org_module_ord_idx'),
        ),
    ]
