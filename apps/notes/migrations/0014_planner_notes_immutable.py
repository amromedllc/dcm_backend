from django.db import migrations


def install_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    # Unqualified identifiers deliberately resolve inside the tenant schema
    # selected by migrate_schemas, rather than pinning this to public.
    schema_editor.execute('''
        CREATE FUNCTION planner_note_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Planner note provenance, addenda and signatures are immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER planner_note_application_immutable
        BEFORE UPDATE OR DELETE ON notes_notecontentapplication
        FOR EACH ROW EXECUTE FUNCTION planner_note_reject_mutation();
        CREATE TRIGGER planner_note_application_no_truncate
        BEFORE TRUNCATE ON notes_notecontentapplication
        FOR EACH STATEMENT EXECUTE FUNCTION planner_note_reject_mutation();
        CREATE TRIGGER planner_note_addendum_immutable
        BEFORE UPDATE OR DELETE ON notes_noteaddendum
        FOR EACH ROW EXECUTE FUNCTION planner_note_reject_mutation();
        CREATE TRIGGER planner_note_addendum_no_truncate
        BEFORE TRUNCATE ON notes_noteaddendum
        FOR EACH STATEMENT EXECUTE FUNCTION planner_note_reject_mutation();
        CREATE TRIGGER planner_note_signature_immutable
        BEFORE UPDATE OR DELETE ON notes_notesignature
        FOR EACH ROW EXECUTE FUNCTION planner_note_reject_mutation();
        CREATE TRIGGER planner_note_signature_no_truncate
        BEFORE TRUNCATE ON notes_notesignature
        FOR EACH STATEMENT EXECUTE FUNCTION planner_note_reject_mutation();
    ''')


def remove_triggers(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('''
            DROP TRIGGER planner_note_application_immutable ON notes_notecontentapplication;
            DROP TRIGGER planner_note_application_no_truncate ON notes_notecontentapplication;
            DROP TRIGGER planner_note_addendum_immutable ON notes_noteaddendum;
            DROP TRIGGER planner_note_addendum_no_truncate ON notes_noteaddendum;
            DROP TRIGGER planner_note_signature_immutable ON notes_notesignature;
            DROP TRIGGER planner_note_signature_no_truncate ON notes_notesignature;
            DROP FUNCTION planner_note_reject_mutation();
        ''')


class Migration(migrations.Migration):

    dependencies = [
        ('notes', '0013_planner_notes'),
    ]

    operations = [migrations.RunPython(install_triggers, remove_triggers)]
