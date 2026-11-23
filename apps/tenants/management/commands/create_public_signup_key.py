from django.core.management.base import BaseCommand

from apps.tenants.models import PublicSignupKey


class Command(BaseCommand):
    help = (
        'Mint a platform-wide key for the public demo-signup endpoint '
        '(POST /api/v1/public/signup). The raw key is shown once — store it '
        'securely (e.g. in the marketing site\'s own secrets store) and send '
        'it as the X-Signup-Key header.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--name', required=True, help='Label for this key, e.g. "Marketing site"')

    def handle(self, *args, **options):
        key, raw_key = PublicSignupKey.generate(name=options['name'])
        self.stdout.write(self.style.SUCCESS(f'Created signup key "{key.name}" (id={key.id})'))
        self.stdout.write('Raw key (shown once, store it now):')
        self.stdout.write(self.style.WARNING(raw_key))
