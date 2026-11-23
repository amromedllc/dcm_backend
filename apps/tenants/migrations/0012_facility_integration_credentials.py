from django.db import migrations, models


def move_connections(apps, schema_editor):
    Organization = apps.get_model('tenants', 'Organization')
    Facility = apps.get_model('tenants', 'OrganizationTpmsAdminId')
    for org in Organization.objects.exclude(integration_admin_id=None).iterator():
        moved = Facility.objects.filter(organization_id=org.pk, admin_id=org.integration_admin_id).update(
            integration_platform=org.integration_platform,
            integration_email=org.integration_email,
            integration_password_encrypted=org.integration_password_encrypted,
            integration_admin_id=org.integration_admin_id,
        )
        if not moved and org.integration_password_encrypted:
            raise RuntimeError(f'Organization {org.pk} needs its connected TherapyPMS facility mapped before migration.')
    # The facility record is now the sole owner of the stored credentials.
    Organization.objects.update(integration_platform='', integration_email='',
                                integration_password_encrypted='', integration_admin_id=None)


class Migration(migrations.Migration):
    dependencies = [('tenants', '0011_alter_organization_automatic_logout_minutes')]
    operations = [
        migrations.AddField(model_name='organizationtpmsadminid', name='integration_platform', field=models.CharField(max_length=30, blank=True, default='')),
        migrations.AddField(model_name='organizationtpmsadminid', name='integration_email', field=models.EmailField(max_length=254, blank=True, default='')),
        migrations.AddField(model_name='organizationtpmsadminid', name='integration_password_encrypted', field=models.TextField(blank=True, default='')),
        migrations.AddField(model_name='organizationtpmsadminid', name='integration_admin_id', field=models.IntegerField(null=True, blank=True)),
        migrations.AddField(model_name='organizationtpmsadminid', name='updated_at', field=models.DateTimeField(auto_now=True)),
        migrations.RunPython(move_connections, migrations.RunPython.noop),
    ]
