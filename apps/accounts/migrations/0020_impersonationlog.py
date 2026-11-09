import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0019_passwordresetlink'),
    ]

    operations = [
        migrations.CreateModel(
            name='ImpersonationLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('started_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('ended_at', models.DateTimeField(null=True, blank=True)),
                ('ip_address', models.GenericIPAddressField(null=True, blank=True)),
                ('session_id', models.CharField(max_length=64, db_index=True)),
                ('status', models.CharField(
                    max_length=20,
                    choices=[('active', 'Active'), ('ended', 'Ended'), ('expired', 'Expired')],
                    default='active',
                    db_index=True,
                )),
                ('reason', models.TextField(blank=True, default='')),
                ('admin', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='impersonation_sessions_initiated',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('target_user', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='impersonation_sessions_received',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'app_label': 'accounts',
                'ordering': ['-started_at'],
            },
        ),
    ]
