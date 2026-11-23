"""Synthetic reminder suite: no production settings, environment files or services."""
SECRET_KEY = 'synthetic-reminder-tests-only'
INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes', 'apps.tenants', 'apps.accounts',
    'apps.clients', 'apps.programs', 'apps.sessions', 'apps.audit', 'apps.notes',
    'apps.behavior_plans', 'apps.caregiver_portal', 'apps.analytics', 'apps.exports',
    'apps.integrations', 'apps.notifications', 'apps.central_library', 'apps.legacy',
]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
MIGRATION_MODULES = {app.rsplit('.', 1)[-1]: None for app in INSTALLED_APPS}
MIGRATION_MODULES['dcm_sessions'] = None
USE_TZ = True
TIME_ZONE = 'UTC'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'accounts.User'
TENANT_MODEL = 'tenants.Organization'
TENANT_DOMAIN_MODEL = 'tenants.Domain'
ROOT_URLCONF = 'apps.notifications.tests.reminder_urls'
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': ['templates'], 'APP_DIRS': True}]
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
DEFAULT_FROM_EMAIL = 'reminders@example.test'
FRONTEND_BASE_URL = 'https://app.example.test'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
