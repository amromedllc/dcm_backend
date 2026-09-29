from unittest import mock

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.accounts.models import User
from apps.integrations.models import Provider
from apps.integrations.tpms_pull import pull_providers
from apps.tenants.models import Organization
from shared.tenancy import tenant_context

PRACTICE_ADMIN_ID = 555


class PullProvidersTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Pull Org', slug='pull-org', schema_name='pull_org',
            integration_platform=Organization.IntegrationPlatform.THERAPY_PMS,
            integration_admin_id=PRACTICE_ADMIN_ID,
        )
        self.token_patcher = mock.patch('apps.integrations.tpms_pull._admin_access_token', return_value='fake-token')
        self.token_patcher.start()
        self.addCleanup(self.token_patcher.stop)

    def _row(self, **overrides):
        row = {
            'id': 42,
            'email': 'provider@example.com',
            'first_name': 'Dana',
            'last_name': 'Ray',
            'employee_type': 'BCBA',
            'is_active': True,
        }
        row.update(overrides)
        return row

    def test_creates_both_a_user_and_a_provider_row(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch('apps.integrations.tpms_pull.admin_list_providers', return_value=[self._row()]):
                result = pull_providers(self.org)

            self.assertEqual(result.created, 1)
            user = User.objects.get(external_employee_id=42)
            self.assertEqual(user.email, 'provider@example.com')
            self.assertEqual(user.role, User.Role.SUPERVISOR)  # BCBA maps to supervisor

            provider = Provider.objects.get(external_employee_id=42, external_admin_id=PRACTICE_ADMIN_ID)
            self.assertEqual(provider.email, 'provider@example.com')
            self.assertEqual(provider.employee_type, 'BCBA')
            self.assertEqual(provider.user_id, user.id)

    def test_provider_row_persists_and_updates_independently_of_the_user_account(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch('apps.integrations.tpms_pull.admin_list_providers', return_value=[self._row()]):
                pull_providers(self.org)

            user = User.objects.get(external_employee_id=42)
            # Delete the login account — the Provider directory row should survive independently.
            user.delete()
            self.assertTrue(Provider.objects.filter(external_employee_id=42).exists())

            with mock.patch('apps.integrations.tpms_pull.admin_list_providers', return_value=[self._row(last_name='Ray-Updated')]):
                result = pull_providers(self.org)

            self.assertEqual(result.created, 1)  # the User got re-created since it was deleted
            provider = Provider.objects.get(external_employee_id=42, external_admin_id=PRACTICE_ADMIN_ID)
            self.assertEqual(provider.last_name, 'Ray-Updated')

    def test_unchanged_row_is_skipped_on_second_pull(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch('apps.integrations.tpms_pull.admin_list_providers', return_value=[self._row()]):
                pull_providers(self.org)
                result = pull_providers(self.org)

            self.assertEqual(result.created, 0)
            self.assertEqual(result.updated, 0)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(Provider.objects.filter(external_employee_id=42).count(), 1)
