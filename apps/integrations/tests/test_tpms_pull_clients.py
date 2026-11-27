from unittest import mock

from django.test import TestCase
from django_tenants.utils import schema_context

from apps.clients.models import Client
from apps.integrations.tpms_pull import pull_clients
from apps.tenants.models import Organization
from shared.tenancy import tenant_context


PRACTICE_ADMIN_ID = 101
SAMPLE_CLIENT = {
    'id': 4812,
    'is_active_client': 1,
    'client_full_name': 'Avery J Example',
    'client_first_name': 'Avery',
    'client_middle': 'J',
    'client_last_name': 'Example',
    'client_gender': 'Female',
    'client_dob': '2012-03-14',
    'email': 'avery@example.test',
    'email_type': 'Work',
    'phone_number': '555-0102',
}


class PullClientsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(
            name='Client Sync Test', slug='client-sync-test', schema_name='client_sync_test',
            integration_platform=Organization.IntegrationPlatform.THERAPY_PMS,
            integration_admin_id=PRACTICE_ADMIN_ID,
        )
        self.token_patcher = mock.patch('apps.integrations.tpms_pull._admin_access_token', return_value='synthetic-token')
        self.token_patcher.start()
        self.addCleanup(self.token_patcher.stop)

    def test_pull_saves_client_contact_and_demographic_fields(self):
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            with mock.patch('apps.integrations.tpms_pull.admin_list_clients', return_value=[SAMPLE_CLIENT]):
                result = pull_clients(self.org)

            self.assertEqual(result.created, 1)
            client = Client.objects.get(external_id='4812')
            self.assertEqual(client.external_admin_id, PRACTICE_ADMIN_ID)
            self.assertEqual(client.first_name, 'Avery')
            self.assertEqual(client.middle_name, 'J')
            self.assertEqual(client.last_name, 'Example')
            self.assertEqual(client.gender, 'Female')
            self.assertEqual(client.email, 'avery@example.test')
            self.assertEqual(client.email_type, 'Work')
            self.assertEqual(client.phone_number, '555-0102')

    def test_pull_updates_contact_details_on_later_sync(self):
        updated = {**SAMPLE_CLIENT, 'email': 'updated@example.test', 'phone_number': '555-0134'}
        with schema_context(self.org.schema_name), tenant_context(self.org.pk):
            Client.objects.create(
                external_id='4812', external_admin_id=PRACTICE_ADMIN_ID,
                first_name='Avery', middle_name='J', last_name='Example',
            )
            with mock.patch('apps.integrations.tpms_pull.admin_list_clients', return_value=[updated]):
                result = pull_clients(self.org)

            client = Client.objects.get(external_id='4812')
            self.assertEqual(result.updated, 1)
            self.assertEqual(client.email, 'updated@example.test')
            self.assertEqual(client.phone_number, '555-0134')
