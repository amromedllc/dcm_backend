from django.db import migrations


def install_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    # Unqualified identifiers deliberately resolve inside the tenant schema
    # selected by migrate_schemas, rather than pinning this to public.
    schema_editor.execute('''
        CREATE FUNCTION planner_snapshot_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Planner content snapshots are immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER planner_snapshot_immutable
        BEFORE UPDATE OR DELETE ON integrations_plannercontentsnapshot
        FOR EACH ROW EXECUTE FUNCTION planner_snapshot_reject_mutation();
        CREATE TRIGGER planner_snapshot_no_truncate
        BEFORE TRUNCATE ON integrations_plannercontentsnapshot
        FOR EACH STATEMENT EXECUTE FUNCTION planner_snapshot_reject_mutation();
    ''')


def remove_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('''
            DROP TRIGGER planner_snapshot_immutable ON integrations_plannercontentsnapshot;
            DROP TRIGGER planner_snapshot_no_truncate ON integrations_plannercontentsnapshot;
            DROP FUNCTION planner_snapshot_reject_mutation();
        ''')


class Migration(migrations.Migration):
    dependencies = [('integrations', '0004_planner_foundation')]
    operations = [migrations.RunPython(install_trigger, remove_trigger)]
