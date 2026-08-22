from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0025_programfolder_program_folder'),
        ('tenants', '0003_remove_organization_tpms_admin_id_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProgramModule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200)),
                ('display_order', models.PositiveIntegerField(default=0, db_index=True)),
                ('program', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='modules',
                    to='programs.program',
                )),
                ('organization', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    to='tenants.organization',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, db_constraint=False, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'app_label': 'programs',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='ProgramSubmodule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200)),
                ('display_order', models.PositiveIntegerField(default=0, db_index=True)),
                ('module', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='submodules',
                    to='programs.programmodule',
                )),
                ('organization', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    to='tenants.organization',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, db_constraint=False, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'app_label': 'programs',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='target',
            name='module',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='targets',
                to='programs.programmodule',
            ),
        ),
        migrations.AddField(
            model_name='target',
            name='submodule',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='targets',
                to='programs.programsubmodule',
            ),
        ),
    ]
