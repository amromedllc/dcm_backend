import re
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import schema_context

from apps.tenants.models import Organization, Domain, OrganizationTpmsAdminId


class Command(BaseCommand):
    help = 'Provision a new organization (tenant schema + domain + admin user)'

    def add_arguments(self, parser):
        parser.add_argument('--name', required=True, help='Organization display name, e.g. "AmroMed"')
        parser.add_argument('--schema', required=True, help='Postgres schema name (lowercase, no spaces), e.g. "amromed"')
        parser.add_argument('--domain', required=True, help='Primary hostname, e.g. "amromed.dcm.app" or "localhost"')
        parser.add_argument('--plan', default='starter', choices=['starter', 'professional', 'enterprise'])
        parser.add_argument('--admin-email', dest='admin_email', help='Create an admin user inside the new schema')
        parser.add_argument('--admin-password', dest='admin_password', help='Password for the admin user')
        parser.add_argument(
            '--tpms-admin-id', dest='tpms_admin_ids', type=int, action='append', default=None,
            help=(
                'TherapyPMS practice admin id this org maps to — required for staff to log in via '
                'TPMS credentials. Repeat the flag to map multiple practices to this org, '
                'e.g. --tpms-admin-id 101 --tpms-admin-id 202'
            ),
        )

    def handle(self, *args, **options):
        name = options['name']
        schema = options['schema'].lower()
        domain = options['domain'].lower()
        plan = options['plan']
        admin_email = options.get('admin_email')
        admin_password = options.get('admin_password')
        tpms_admin_ids = options.get('tpms_admin_ids') or []

        if not re.match(r'^[a-z][a-z0-9_]{1,61}$', schema):
            raise CommandError(
                'Schema name must start with a letter, contain only lowercase letters/digits/underscores, '
                'and be 2–62 characters.'
            )

        if Organization.objects.filter(schema_name=schema).exists():
            raise CommandError(f'Schema "{schema}" already exists.')

        if Domain.objects.filter(domain=domain).exists():
            raise CommandError(f'Domain "{domain}" is already registered.')

        if admin_email and not admin_password:
            raise CommandError('--admin-password is required when --admin-email is provided.')

        already_mapped = OrganizationTpmsAdminId.objects.filter(admin_id__in=tpms_admin_ids)
        if already_mapped.exists():
            taken = ', '.join(str(a) for a in already_mapped.values_list('admin_id', flat=True))
            raise CommandError(f'TPMS admin id(s) {taken} already mapped to another organization.')

        self.stdout.write(f'Creating organization "{name}" …')

        with transaction.atomic():
            org = Organization(schema_name=schema, name=name, slug=schema, plan=plan)
            org.save()  # triggers auto_create_schema

            Domain.objects.create(tenant=org, domain=domain, is_primary=True)
            self.stdout.write(self.style.SUCCESS(f'  Schema "{schema}" created'))
            self.stdout.write(self.style.SUCCESS(f'  Domain "{domain}" registered'))

            for admin_id in tpms_admin_ids:
                OrganizationTpmsAdminId.objects.create(organization=org, admin_id=admin_id)
            if tpms_admin_ids:
                self.stdout.write(self.style.SUCCESS(f'  TPMS admin id(s) mapped: {tpms_admin_ids}'))

        if admin_email:
            with schema_context(schema):
                from apps.accounts.models import User
                if User.objects.filter(email=admin_email).exists():
                    self.stdout.write(self.style.WARNING(f'  User {admin_email} already exists — skipping'))
                else:
                    User.objects.create_superuser(
                        email=admin_email,
                        password=admin_password,
                        first_name='Admin',
                        last_name=name,
                        role='admin',
                        organization=org,
                    )
                    self.stdout.write(self.style.SUCCESS(f'  Admin user "{admin_email}" created'))

        self.stdout.write(self.style.SUCCESS(f'\nDone. Organization "{name}" is ready.'))
