# Generated migration

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0008_clientfavorite'),
        ('tenants', '0009_organization_integration_credentials'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ClientDirectoryView',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('visibility', models.CharField(
                    choices=[('me', 'Only me'), ('all', 'All users'), ('role', 'Specific role')],
                    default='me',
                    max_length=10,
                )),
                ('visibility_role', models.CharField(blank=True, max_length=20)),
                ('columns', models.JSONField(default=list)),
                ('status_filter', models.CharField(blank=True, max_length=20)),
                ('organization', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    to='tenants.organization',
                )),
                ('created_by', models.ForeignKey(
                    db_constraint=False,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='client_directory_views',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-id'],
            },
        ),
    ]
