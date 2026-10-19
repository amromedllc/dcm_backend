"""Replace only the public Central Library, with a mandatory pre-change backup."""
import json
from pathlib import Path

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django_tenants.utils import get_public_schema_name, schema_context

from apps.central_library.catalog_import import build_catalog
from apps.central_library.models import CentralProgram, CentralProgramFolder, CentralTarget


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
        if not options['replace']:
            self.stdout.write('Dry run: no database changes. Use --replace --backup-file PATH to apply.')
            return
        if not options['backup_file']:
            raise CommandError('--backup-file is required with --replace')
        if connection.vendor != 'postgresql':
            raise CommandError('Replacement requires the configured PostgreSQL database')

        with schema_context(get_public_schema_name()), transaction.atomic():
            # Protect the backup/replacement against concurrent catalog edits.
            with connection.cursor() as cursor:
                cursor.execute('SET LOCAL lock_timeout = \'10s\'')
                tables = ', '.join(connection.ops.quote_name(model._meta.db_table) for model in
                                   (CentralProgramFolder, CentralProgram, CentralTarget))
                cursor.execute(f'LOCK TABLE {tables} IN EXCLUSIVE MODE')
            records = [
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
            old_programs = CentralProgram.objects.count()
            old_targets = CentralTarget.objects.count()
            CentralProgram.objects.all().delete()
            CentralProgramFolder.objects.all().delete()
            folders = {
                name: CentralProgramFolder.objects.create(name=name, display_order=i * 10)
                for i, name in enumerate(domains)
            }
            for item in catalog:
                program = CentralProgram.objects.create(folder=folders[item['domain']], **item['fields'])
                CentralTarget.objects.bulk_create([
                    CentralTarget(program=program, **target['fields']) for target in item['targets']
                ])
            actual = (CentralProgram.objects.count(), CentralTarget.objects.count(), CentralProgramFolder.objects.count())
            if actual != (len(catalog), total, len(domains)):
                raise CommandError(f'Post-import counts differ: {actual}; transaction rolled back')
        self.stdout.write(self.style.SUCCESS(
            f'Replaced {old_programs} programs / {old_targets} targets with '
            f'{len(catalog)} programs / {total} targets in {len(domains)} folders. '
            f'Backup: {options["backup_file"]}. Organization/client records were not modified.'
        ))
