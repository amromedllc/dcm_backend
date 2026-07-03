"""
Management command: bulk-import clients from the TherapyPMS source database into DCM.

Appointments are read live from TPMS on every request and do not need syncing.
Run this once when setting up a new tenant to pre-populate DCM Client records.

Usage:
    python manage.py sync_tpms --tenant acme
    python manage.py sync_tpms --tenant acme --admin-id 5
    python manage.py sync_tpms --all-tenants
"""
from django_tenants.utils import tenant_context
from django.core.management.base import BaseCommand, CommandError

from apps.integrations.sync import sync_tpms_clients
from apps.tenants.models import Organization


class Command(BaseCommand):
    help = 'Bulk-import clients from the TherapyPMS database into a DCM tenant'

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument(
            '--tenant',
            metavar='SLUG',
            help='Organization slug to sync into (e.g. acme)',
        )
        target.add_argument(
            '--all-tenants',
            action='store_true',
            help='Sync into every active organization',
        )
        parser.add_argument(
            '--admin-id',
            type=int,
            default=None,
            metavar='ID',
            help='Restrict sync to a single TPMS admin/facility ID',
        )

    def handle(self, *args, **options):
        admin_id = options['admin_id']

        if options['all_tenants']:
            tenants = list(Organization.objects.filter(is_active=True).exclude(schema_name='public'))
        else:
            slug = options['tenant']
            try:
                tenants = [Organization.objects.get(slug=slug)]
            except Organization.DoesNotExist:
                raise CommandError(f'No organization found with slug "{slug}"')

        for org in tenants:
            self.stdout.write(f'\n--- Tenant: {org.name} (schema: {org.schema_name}) ---')
            with tenant_context(org):
                scope = f'admin_id={admin_id}' if admin_id else 'all facilities'
                self.stdout.write(f'  Syncing clients ({scope}) …')
                result = sync_tpms_clients(admin_id=admin_id)
                self.stdout.write(self.style.SUCCESS(f'  Clients — {result}'))
                for err in result.errors:
                    self.stderr.write(f'  ERROR: {err}')

        self.stdout.write(self.style.SUCCESS('\nAll done.'))
