from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0006_assessmentrecord'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='graphannotation',
            index=models.Index(fields=['organization', 'program', 'date'], name='graph_org_program_date_idx'),
        ),
        migrations.AddIndex(
            model_name='graphannotation',
            index=models.Index(fields=['organization', 'target', 'date'], name='graph_org_target_date_idx'),
        ),
        migrations.AddIndex(
            model_name='clientannotation',
            index=models.Index(fields=['organization', 'external_client_id', 'date'], name='clientann_org_client_date_idx'),
        ),
        migrations.AddIndex(
            model_name='savedinsightgraph',
            index=models.Index(fields=['organization', 'program', 'display_order'], name='insight_org_program_ord_idx'),
        ),
        migrations.AddIndex(
            model_name='savedinsightgraph',
            index=models.Index(fields=['organization', 'external_client_id', 'display_order'], name='insight_org_client_ord_idx'),
        ),
        migrations.AddIndex(
            model_name='assessmentrecord',
            index=models.Index(fields=['organization', 'external_client_id', 'assessed_on'], name='assess_org_client_date_idx'),
        ),
        migrations.AddIndex(
            model_name='assessmentrecord',
            index=models.Index(fields=['organization', 'external_client_id', 'assessment_name', 'assessed_on'], name='assess_org_name_date_idx'),
        ),
    ]
