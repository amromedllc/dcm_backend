from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_sub_items(apps, schema_editor):
    Target = apps.get_model('programs', 'Target')
    TargetSubItem = apps.get_model('programs', 'TargetSubItem')
    for target in Target.objects.exclude(sub_items=[]):
        created = []
        for idx, item in enumerate(target.sub_items or []):
            if not isinstance(item, dict):
                continue
            key = item.get('key') or f'item_{idx + 1}'
            label = item.get('label') or key
            status = item.get('status')
            if not status:
                if target.sub_item_progression == 'total_task':
                    status = 'acquisition'
                elif target.sub_item_progression == 'backward':
                    status = 'acquisition' if idx == len(target.sub_items) - 1 else 'waiting'
                else:
                    status = 'acquisition' if idx == 0 else 'waiting'
            TargetSubItem.objects.create(
                organization_id=target.organization_id,
                created_by_id=target.created_by_id,
                target_id=target.id,
                key=key,
                label=label,
                status=status,
                display_order=idx,
            )
            created.append({'key': key, 'label': label, 'status': status})
        if created:
            target.sub_items = created
            target.save(update_fields=['sub_items'])


def unseed_sub_items(apps, schema_editor):
    TargetSubItem = apps.get_model('programs', 'TargetSubItem')
    TargetSubItem.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0026_add_program_modules'),
        ('tenants', '0003_remove_organization_tpms_admin_id_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='target',
            name='sub_item_progression',
            field=models.CharField(
                choices=[
                    ('forward', 'Forward Chain'),
                    ('backward', 'Backward Chain'),
                    ('total_task', 'Total Task'),
                ],
                default='forward',
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='TargetSubItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.CharField(max_length=100)),
                ('label', models.CharField(max_length=200)),
                ('status', models.CharField(
                    choices=[
                        ('waiting', 'Waiting'),
                        ('probe', 'Probe'),
                        ('acquisition', 'Acquisition'),
                        ('mastered', 'Mastered'),
                        ('closed', 'Closed'),
                        ('hold', 'Hold'),
                        ('discontinued', 'Discontinued'),
                    ],
                    db_index=True,
                    default='waiting',
                    max_length=20,
                )),
                ('display_order', models.PositiveIntegerField(db_index=True, default=0)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='tenants.organization')),
                ('target', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='child_items', to='programs.target')),
            ],
            options={
                'app_label': 'programs',
                'ordering': ['display_order', 'id'],
                'unique_together': {('target', 'key')},
            },
        ),
        migrations.CreateModel(
            name='TargetSubItemStatusChange',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('from_status', models.CharField(max_length=20)),
                ('to_status', models.CharField(max_length=20)),
                ('trigger', models.CharField(
                    choices=[
                        ('manual', 'Manual'),
                        ('auto_mastery', 'Automatic — Mastery Criteria Met'),
                    ],
                    default='manual',
                    max_length=20,
                )),
                ('session_run_id', models.PositiveIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='tenants.organization')),
                ('sub_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='status_changes', to='programs.targetsubitem')),
            ],
            options={
                'app_label': 'programs',
                'ordering': ['-created_at'],
            },
        ),
        migrations.RunPython(seed_sub_items, unseed_sub_items),
    ]
