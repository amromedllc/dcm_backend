"""Replace only the public Central Library, with a mandatory pre-change backup."""
import json
from pathlib import Path

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

from apps.central_library.catalog_import import build_catalog, build_settings
from apps.central_library.settings_service import sync_legacy_links
from apps.central_library.models import CentralProgram, CentralProgramFolder, CentralTarget, CentralProgramSetting, CentralSettingCopy


def _bulk_import_catalog(catalog, folders, settings):
    """Apply authoritative v2 rows and links in batches, preserving record IDs."""
    now = timezone.now()
    programs = {row.source_id: row for row in CentralProgram.objects.filter(
        source_id__in=[item['source_id'] for item in catalog])}
    new_programs, changed_programs, program_fields = [], [], set()
    for item in catalog:
        program = programs.get(item['source_id'])
        is_new = program is None
        if is_new:
            program = CentralProgram(source_id=item['source_id'])
        fields = dict(item['fields'])
        configuration = dict(fields.get('configuration', {}))
        if configuration.get('custom_field_values'):
            configuration['custom_field_values'] = {
                str(settings[ref].pk): value
                for ref, value in configuration['custom_field_values'].items()}
            fields['configuration'] = configuration
        for field, value in fields.items():
            setattr(program, field, value)
        program.folder = folders[item['domain']]
        program.updated_at = now
        program_fields.update(fields)
        (new_programs if is_new else changed_programs).append(program)
        programs[item['source_id']] = program
    CentralProgram.objects.bulk_create(new_programs, batch_size=500)
    if changed_programs:
        CentralProgram.objects.bulk_update(changed_programs, sorted(program_fields | {'folder', 'updated_at'}), batch_size=100)
    program_ids = [program.pk for program in programs.values()]
    links = CentralProgram.settings.through
    links.objects.filter(centralprogram_id__in=program_ids).delete()
    links.objects.bulk_create([
        links(centralprogram_id=programs[item['source_id']].pk, centralprogramsetting_id=settings[ref].pk)
        for item in catalog for ref in item.get('setting_refs', [])], batch_size=500)

    target_rows = [target for item in catalog for target in item['targets']]
    targets = {row.source_id: row for row in CentralTarget.objects.filter(
        source_id__in=[target['source_id'] for target in target_rows])}
    new_targets, changed_targets, target_fields = [], [], set()
    for item in catalog:
        for source in item['targets']:
            target = targets.get(source['source_id'])
            is_new = target is None
            if is_new:
                target = CentralTarget(source_id=source['source_id'])
            for field, value in source['fields'].items():
                setattr(target, field, value)
            target.program = programs[item['source_id']]
            target_fields.update(source['fields'])
            (new_targets if is_new else changed_targets).append(target)
            targets[source['source_id']] = target
    CentralTarget.objects.bulk_create(new_targets, batch_size=500)
    if changed_targets:
        CentralTarget.objects.bulk_update(changed_targets, sorted(target_fields | {'program'}), batch_size=100)
    target_ids = [target.pk for target in targets.values()]
    links = CentralTarget.settings.through
    links.objects.filter(centraltarget_id__in=target_ids).delete()
    links.objects.bulk_create([
        links(centraltarget_id=targets[source['source_id']].pk, centralprogramsetting_id=settings[ref].pk)
        for source in target_rows for ref in source.get('setting_refs', [])], batch_size=500)
    return program_ids, target_ids


class Command(BaseCommand):
    help = 'Validate a workbook JSON export; --replace backs up and replaces the public Central Library'

    def add_arguments(self, parser):
        parser.add_argument('catalog')
        parser.add_argument('--replace', action='store_true')
        parser.add_argument('--backup-file', help='New JSON fixture path; required with --replace')

    def handle(self, *args, **options):
        try:
            document = json.loads(Path(options['catalog']).read_text())
            catalog = build_catalog(document)
            setting_rows = build_settings(document)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CommandError(f'Invalid catalog: {exc}') from exc
        total = sum(len(program['targets']) for program in catalog)
        domains = list(dict.fromkeys(program['domain'] for program in catalog))
        deferred = sum(
            target['fields']['measurement_type'] == 'instructions'
            for program in catalog for target in program['targets']
        )
        self.stdout.write(f'Validated: {len(catalog)} programs, {total} targets, {len(domains)} folders; '
                          f'{deferred} instruction-only targets require configuration.')
        self.stdout.write(f'Workbook settings: {len(setting_rows)}; links: '
                          f'{sum(len(p.get("setting_refs", [])) + sum(len(t.get("setting_refs", [])) for t in p["targets"]) for p in catalog)}')
        if not options['replace']:
            self.stdout.write('Dry run: no database changes. Use --replace --backup-file PATH to apply.')
            return
        if not options['backup_file']:
            raise CommandError('--backup-file is required with --replace')
        if connection.vendor != 'postgresql':
            raise CommandError('Replacement requires the configured PostgreSQL database')

        self.stdout.write('Connecting to database and starting catalog transaction...')
        self.stdout.flush()
        with schema_context(get_public_schema_name()), transaction.atomic():
            self.stdout.write('Acquiring central catalog locks (10-second lock timeout)...')
            self.stdout.flush()
            # Protect the backup/replacement against concurrent catalog edits.
            with connection.cursor() as cursor:
                cursor.execute('SET LOCAL lock_timeout = \'10s\'')
                tables = ', '.join(connection.ops.quote_name(model._meta.db_table) for model in
                                   (CentralProgramSetting, CentralProgramFolder, CentralProgram, CentralTarget))
                cursor.execute(f'LOCK TABLE {tables} IN EXCLUSIVE MODE')
            self.stdout.write('Locks acquired. Reading existing catalog for backup...')
            self.stdout.flush()
            records = [
                *CentralProgramSetting.objects.order_by('id'),
                *CentralSettingCopy.objects.order_by('id'),
                *CentralProgramFolder.objects.order_by('id'),
                *CentralProgram.objects.order_by('id'),
                *CentralTarget.objects.order_by('id'),
            ]
            backup = serializers.serialize('json', records, indent=2)
            try:
                # Exclusive create: never clobber an earlier backup.
                with Path(options['backup_file']).open('x', encoding='utf-8') as stream:
                    stream.write(backup)
                    stream.flush()
                    import os
                    os.fsync(stream.fileno())
            except OSError as exc:
                raise CommandError(f'Backup failed; library was not changed: {exc}') from exc
            self.stdout.write(f'Backup saved: {options["backup_file"]}. Importing shared settings...')
            self.stdout.flush()
            old_programs = CentralProgram.objects.count()
            old_targets = CentralTarget.objects.count()
            settings = {}
            for identity, row in setting_rows.items():
                existing = CentralProgramSetting.objects.filter(source_id=identity).first()
                if existing is None:
                    existing = CentralProgramSetting.objects.filter(kind=row['kind'], name=row['name']).first()
                if existing is None and row['kind'] in {'prompting', 'workflow'}:
                    existing = CentralProgramSetting.objects.filter(
                        kind=row['kind'], configuration=row['configuration'],
                    ).order_by('pk').first()
                if existing is not None and existing.kind != row['kind']:
                    raise CommandError(f'{identity}: cannot change an existing setting kind')
                if existing is None:
                    existing = CentralProgramSetting(source_id=identity)
                existing.source_id = identity
                existing.kind, existing.name = row['kind'], row['name']
                existing.configuration, existing.is_active = row['configuration'], row['ready']
                existing.save()
                settings[identity] = existing
            for identity, row in setting_rows.items():
                settings[identity].dependencies.set([settings[ref] for ref in row['dependencies']])
            folders = {
                name: CentralProgramFolder.objects.update_or_create(name=name, defaults={'display_order': i * 10})[0]
                for i, name in enumerate(domains)
            }
            if document['format'] == 'progressly-workbook-v2':
                self.stdout.write(f'Importing {len(catalog)} programs and {total} targets in batches...')
                self.stdout.flush()
                program_ids, target_ids = _bulk_import_catalog(catalog, folders, settings)
                self.stdout.write('Program, target, and shared-setting links saved. Verifying catalog...')
                self.stdout.flush()
            else:
                program_ids, target_ids = [], []
                for program_index, item in enumerate(catalog, start=1):
                    if program_index == 1 or program_index % 10 == 0:
                        self.stdout.write(f'Importing program {program_index}/{len(catalog)}: {item["fields"]["name"]}')
                        self.stdout.flush()
                    fields = dict(item['fields'])
                    # Workbook custom-field references become central setting IDs;
                    # facility imports will remap those IDs again to local fields.
                    configuration = dict(fields.get('configuration', {}))
                    if configuration.get('custom_field_values'):
                        configuration['custom_field_values'] = {
                            str(settings[ref].pk): value
                            for ref, value in configuration['custom_field_values'].items()
                        }
                        fields['configuration'] = configuration
                    program, _ = CentralProgram.objects.update_or_create(
                        source_id=item['source_id'], defaults={'folder': folders[item['domain']], **fields})
                    program_ids.append(program.pk)
                    if setting_rows or document['format'] == 'progressly-workbook-v2':
                        program.settings.set([settings[ref] for ref in item.get('setting_refs', [])])
                    sync_legacy_links(program)
                    for source_target in item['targets']:
                        target, _ = CentralTarget.objects.update_or_create(source_id=source_target['source_id'],
                            defaults={'program': program, **source_target['fields']})
                        target_ids.append(target.pk)
                        if setting_rows or document['format'] == 'progressly-workbook-v2':
                            target.settings.set([settings[ref] for ref in source_target.get('setting_refs', [])])
                        sync_legacy_links(target)
            # Replace the catalog contents while preserving IDs of matching
            # source entities and their facility source/provenance links.
            CentralTarget.objects.exclude(pk__in=target_ids).delete()
            CentralProgram.objects.exclude(pk__in=program_ids).delete()
            CentralProgramFolder.objects.exclude(pk__in=[folder.pk for folder in folders.values()]).filter(programs__isnull=True).delete()
            actual = (CentralProgram.objects.count(), CentralTarget.objects.count(), CentralProgramFolder.objects.count())
            if actual != (len(catalog), total, len(domains)):
                raise CommandError(f'Post-import counts differ: {actual}; transaction rolled back')
        self.stdout.write(self.style.SUCCESS(
            f'Replaced {old_programs} programs / {old_targets} targets with '
            f'{len(catalog)} programs / {total} targets in {len(domains)} folders. '
            f'Backup: {options["backup_file"]}. Organization/client records were not modified.'
        ))
