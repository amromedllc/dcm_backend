"""Disposable local PG only. Explicit opt-in socket path; no .env or live settings."""
import os
from .planner_settings import *  # noqa: F403

socket_dir = os.environ.get('PLANNER_TEST_PG_SOCKET', '')
if not socket_dir.startswith('/private/tmp/dcm-planner-'):
    raise RuntimeError('Set PLANNER_TEST_PG_SOCKET to a disposable /private/tmp/dcm-planner-* directory')
DATABASES = {'default': {
    'ENGINE': 'django_tenants.postgresql_backend',
    'NAME': 'planner_synthetic', 'USER': 'planner_test', 'PASSWORD': '',
    'HOST': socket_dir, 'PORT': '55439',
    'TEST': {'NAME': 'test_planner_synthetic', 'CHARSET': 'UTF8', 'TEMPLATE': 'template0'},
}}
SHARED_APPS = ['django_tenants', 'django.contrib.contenttypes', 'django.contrib.auth', 'apps.tenants', 'apps.accounts']
TENANT_APPS = ['apps.clients', 'apps.integrations', 'apps.programs', 'apps.sessions', 'apps.notes']
INSTALLED_APPS = SHARED_APPS + TENANT_APPS
DATABASE_ROUTERS = ['django_tenants.routers.TenantSyncRouter']
MIGRATION_MODULES = {}
