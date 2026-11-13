from django.db import migrations


def ensure_probe_default(apps, schema_editor):
    """Repair only Probe in the tenant schema currently being migrated."""
    Organization = apps.get_model('tenants', 'Organization')
    TargetStatus = apps.get_model('programs', 'TargetStatus')
    alias = schema_editor.connection.alias
    schema = schema_editor.connection.schema_name
    # Never touch tenant data while migrating the public schema.
    organizations = Organization.objects.using(alias).filter(schema_name=schema)
    for organization in organizations:
        statuses = TargetStatus.objects.using(alias).filter(organization_id=organization.pk)
        probe, created = statuses.get_or_create(
            key='probe',
            defaults={
                'organization_id': organization.pk,
                'label': 'Probe',
                'icon': 'clipboard-list',
                'color': '#d97706',
                'is_staff_visible': True,
                'is_default': False,
                'is_active': True,
                'display_order': 1,
                'created_by_id': None,
            },
        )
        if not created and probe.created_by_id is not None:
            statuses.filter(pk=probe.pk).update(created_by_id=None)


class Migration(migrations.Migration):
    dependencies = [('programs', '0051_reusable_session_setups')]
    operations = [migrations.RunPython(ensure_probe_default, migrations.RunPython.noop)]
