"""Isolated synthetic SQLite tests. Never imports production or loads .env."""
SECRET_KEY = 'synthetic-test-secret-not-for-deployment'
DEBUG = False
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'accounts.User'
TENANT_MODEL = 'tenants.Organization'
TENANT_DOMAIN_MODEL = 'tenants.Domain'
INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes',
    'apps.tenants', 'apps.accounts', 'apps.clients', 'apps.integrations',
    'apps.programs', 'apps.sessions', 'apps.notes',
]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
MIGRATION_MODULES = {app: None for app in ('auth', 'contenttypes', 'tenants', 'accounts', 'clients', 'integrations', 'programs', 'dcm_sessions', 'notes')}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}}
PRACTICE_PLANNER_PROVIDER = 'disabled'
JWT_SECRET_KEY = 'synthetic-jwt-secret-only-for-tests-with-no-live-credentials'
JWT_ALGORITHM = 'HS256'
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 15
AUTH_SESSION_MAX_HOURS = 12
