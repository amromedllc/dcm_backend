"""Run synthetic checks: python -m apps.analytics.tests.run_measurement_unit_tests.

Uses isolated settings and rejects database access; no local environment is loaded.
"""
import sys
import unittest
from unittest.mock import patch


def main():
    from django.conf import settings
    settings.configure(
        SECRET_KEY='synthetic-local-tests', USE_TZ=True, TIME_ZONE='UTC',
        AUTH_USER_MODEL='accounts.User', TENANT_MODEL='tenants.Organization',
        TENANT_DOMAIN_MODEL='tenants.Domain',
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes'] + [
            'apps.' + name for name in [
                'tenants', 'accounts', 'central_library', 'legacy', 'clients', 'programs',
                'sessions', 'notes', 'behavior_plans', 'caregiver_portal', 'analytics',
                'exports', 'integrations', 'notifications', 'audit',
            ]
        ],
    )
    import django
    django.setup()
    from django.db import connection
    suite = unittest.defaultTestLoader.loadTestsFromName('apps.analytics.tests.test_measurement_calculations')
    with patch.object(connection, 'cursor', side_effect=AssertionError('Database access forbidden in mock tests')):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
