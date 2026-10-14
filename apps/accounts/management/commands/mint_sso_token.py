"""
Management command: mint a TPMS-style SSO token and print a ready-to-open
/sso/login URL, so the TPMS -> DCM SSO hand-off (apps.accounts.api.sso_login)
can be exercised end-to-end without a real TherapyPMS round trip.

Usage:
    python manage.py mint_sso_token --email provider@example.com
    python manage.py mint_sso_token --email provider@example.com --redirect /dashboard
    python manage.py mint_sso_token --email provider@example.com --domain acme.dcm-platform.com
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode

import jwt
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.tenants.models import Domain


class Command(BaseCommand):
    help = 'Mint a TPMS SSO token for an existing DCM user and print the /sso/login URL to test with'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True, help='Email of the existing DCM user to sign in as')
        parser.add_argument('--redirect', default='/', help="Path to land on after a successful SSO login (default: '/')")
        parser.add_argument('--domain', default=None, help="Hostname to build the URL against (default: the user's tenant's first domain)")
        parser.add_argument('--port', default=None, help="Port to append to the domain (e.g. 8001 for `python manage.py runserver 8001`) — the stored Domain row is just the bare hostname, with no port")
        parser.add_argument('--scheme', default='http', choices=['http', 'https'], help='URL scheme (default: http)')
        parser.add_argument('--expires-in', type=int, default=120, help='Token TTL in seconds (default: 120)')

    def handle(self, *args, **options):
        secret = getattr(settings, 'TPMS_SSO_SECRET_KEY', '')
        if not secret:
            raise CommandError('TPMS_SSO_SECRET_KEY is not set — set it in the environment before testing SSO.')

        email = options['email']
        try:
            user = User.objects.exclude(role=User.Role.CAREGIVER).get(email__iexact=email)
        except User.DoesNotExist:
            raise CommandError(f'No non-caregiver DCM user found with email "{email}"')

        if user.external_admin_id is None:
            raise CommandError(
                f'User {email} has no external_admin_id — this SSO flow only works for '
                'TPMS-synced staff/admin accounts, not native/non-TPMS users.'
            )

        domain_host = options['domain']
        if not domain_host:
            domain = Domain.objects.filter(tenant__tpms_admin_ids__admin_id=user.external_admin_id).first()
            if domain is None:
                raise CommandError(
                    f'Could not find a Domain for admin_id={user.external_admin_id} — pass --domain explicitly.'
                )
            domain_host = domain.domain

        if options['port'] and ':' not in domain_host:
            domain_host = f"{domain_host}:{options['port']}"

        now = datetime.now(dt_timezone.utc)
        payload = {
            'email': user.email,
            'adminid': user.external_admin_id,
            'user_type': user.role,
            'iat': now,
            'exp': now + timedelta(seconds=options['expires_in']),
        }
        token = jwt.encode(payload, secret, algorithm='HS256')

        query = urlencode({'token': token, 'redirecturl': options['redirect']})
        url = f"{options['scheme']}://{domain_host}/api/v1/auth/sso/login?{query}"

        self.stdout.write(self.style.SUCCESS('Minted SSO token for:'))
        self.stdout.write(f'  user:        {user.email} (id={user.id}, role={user.role})')
        self.stdout.write(f'  admin_id:    {user.external_admin_id}')
        self.stdout.write(f'  expires in:  {options["expires_in"]}s')
        self.stdout.write('')
        self.stdout.write('Open this URL in a browser to exercise the full SSO + redirect flow:')
        self.stdout.write(self.style.SUCCESS(url))
