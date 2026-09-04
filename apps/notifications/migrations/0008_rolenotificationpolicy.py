import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0007_delete_webpushsubscription'),
        ('tenants', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RoleNotificationPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role', models.CharField(db_index=True, max_length=20)),
                ('event_type', models.CharField(db_index=True, max_length=80)),
                ('email_enabled', models.BooleanField(default=True)),
                ('web_enabled', models.BooleanField(default=True)),
                ('locked', models.BooleanField(default=False)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(blank=True, db_index=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='tenants.organization')),
            ],
            options={
                'ordering': ['role', 'event_type'],
                'unique_together': {('organization', 'role', 'event_type')},
            },
        ),
    ]
