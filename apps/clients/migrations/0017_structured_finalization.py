import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def install_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    # Unqualified identifiers deliberately resolve inside the tenant schema
    # selected by migrate_schemas, rather than pinning this to public.
    schema_editor.execute('''
        CREATE FUNCTION planner_finalized_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Finalized plan signatures and activation history are immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER planner_signature_immutable
        BEFORE UPDATE OR DELETE ON clients_treatmentplansignature
        FOR EACH ROW EXECUTE FUNCTION planner_finalized_reject_mutation();
        CREATE TRIGGER planner_signature_no_truncate
        BEFORE TRUNCATE ON clients_treatmentplansignature
        FOR EACH STATEMENT EXECUTE FUNCTION planner_finalized_reject_mutation();
        CREATE TRIGGER planner_activation_immutable
        BEFORE UPDATE OR DELETE ON clients_planactivation
        FOR EACH ROW EXECUTE FUNCTION planner_finalized_reject_mutation();
        CREATE TRIGGER planner_activation_no_truncate
        BEFORE TRUNCATE ON clients_planactivation
        FOR EACH STATEMENT EXECUTE FUNCTION planner_finalized_reject_mutation();
    ''')


def remove_triggers(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('''
            DROP TRIGGER planner_signature_immutable ON clients_treatmentplansignature;
            DROP TRIGGER planner_signature_no_truncate ON clients_treatmentplansignature;
            DROP TRIGGER planner_activation_immutable ON clients_planactivation;
            DROP TRIGGER planner_activation_no_truncate ON clients_planactivation;
            DROP FUNCTION planner_finalized_reject_mutation();
        ''')


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0016_diagnosis_lock_version'),
        ('tenants', '0007_organizationtpmsadminid_facility_name'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PlanActivation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('effective_from', models.DateField()),
                ('effective_to', models.DateField(blank=True, null=True)),
                ('activated_at', models.DateTimeField(auto_now_add=True)),
                ('activated_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='plan_activations', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='tenants.organization')),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='activations', to='clients.treatmentplan')),
            ],
            options={
                'ordering': ['effective_from', 'id'],
            },
        ),
        migrations.RunPython(install_triggers, remove_triggers),
    ]
