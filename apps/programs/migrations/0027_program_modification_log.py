from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('programs', '0026_add_program_modules'),
        ('tenants', '0003_remove_organization_tpms_admin_id_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProgramModificationLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('external_client_id', models.BigIntegerField(db_index=True)),
                ('note', models.TextField(blank=True)),
                ('add_phase_line', models.BooleanField(default=False)),
                ('phase_line_date', models.DateField(blank=True, null=True)),
                ('phase_line_label', models.CharField(blank=True, max_length=200)),
                ('phase_line_color', models.CharField(default='#0f766e', max_length=7)),
                ('created_by', models.ForeignKey(
                    blank=True,
                    db_constraint=False,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('organization', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    to='tenants.organization',
                )),
                ('program', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='modification_logs',
                    to='programs.program',
                )),
            ],
            options={
                'app_label': 'programs',
                'ordering': ['-created_at'],
            },
        ),
    ]
