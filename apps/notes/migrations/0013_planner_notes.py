# Generated manually to mirror makemigrations output (no local Django env available)

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notes', '0012_lessonnote_old_form_name_old_form_url'),
        ('clients', '0017_structured_finalization'),
        ('integrations', '0005_planner_snapshot_immutable'),
        ('tenants', '0007_organizationtpmsadminid_facility_name'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='notesignature',
            name='content_hash',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.CreateModel(
            name='NoteContentApplication',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('objective_ids', models.JSONField(default=list)),
                ('template_snapshot_ids', models.JSONField(default=list)),
                ('field_key', models.CharField(max_length=100)),
                ('rendered_content', models.TextField()),
                ('applied', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True,
                    on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('note', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='planner_applications', to='notes.lessonnote')),
                ('organization', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='tenants.organization')),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='clients.treatmentplan')),
            ],
            options={'ordering': ['created_at', 'id']},
        ),
        migrations.CreateModel(
            name='NoteAddendum',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('author_name', models.CharField(max_length=200)),
                ('author_role', models.CharField(max_length=50)),
                ('text', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('author', models.ForeignKey(blank=True, db_constraint=False, null=True,
                    on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('note', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='addenda', to='notes.lessonnote')),
                ('organization', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='tenants.organization')),
            ],
            options={'ordering': ['created_at', 'id']},
        ),
        migrations.CreateModel(
            name='ClientHomeworkAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('assigned', 'Assigned'), ('completed', 'Completed')], default='assigned', max_length=20)),
                ('assigned_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('assigned_by', models.ForeignKey(blank=True, db_constraint=False, null=True,
                    on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='homework_assignments', to='clients.client')),
                ('note', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='homework_assignments', to='notes.lessonnote')),
                ('organization', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='tenants.organization')),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='integrations.plannercontentsnapshot')),
            ],
            options={'ordering': ['-assigned_at', '-id']},
        ),
    ]
