import apps.central_library.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('central_library', '0007_knowledgebasemodule_video'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgebasetopic',
            name='video',
            field=models.FileField(
                blank=True,
                max_length=500,
                null=True,
                upload_to=apps.central_library.models._knowledge_base_topic_video_upload_path,
            ),
        ),
        migrations.AddField(
            model_name='knowledgebasetopic',
            name='video_content_type',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='knowledgebasetopic',
            name='video_size',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
