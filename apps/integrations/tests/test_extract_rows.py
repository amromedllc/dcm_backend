from django.test import SimpleTestCase

from apps.integrations.tpms_auth_client import _extract_rows

PROVIDER_ROW = {
    'id': 42,
    'first_name': 'Dana',
    'last_lane': 'Ray',
    'office_email': 'dana@example.com',
}


class ExtractRowsTests(SimpleTestCase):
    def test_finds_rows_under_a_known_block_key(self):
        payload = {'status': 'ok', 'providers': [PROVIDER_ROW]}
        self.assertEqual(_extract_rows(payload, 'providers', 'data'), [PROVIDER_ROW])

    def test_finds_rows_under_the_generic_data_fallback(self):
        payload = {'status': 'ok', 'data': [PROVIDER_ROW]}
        self.assertEqual(_extract_rows(payload, 'providers'), [PROVIDER_ROW])

    def test_finds_rows_under_an_unrecognized_key_via_the_last_resort_scan(self):
        # None of the caller's guessed keys match — this is the exact failure
        # mode reported for /admin/get/providers returning 0 rows.
        payload = {'status': 'ok', 'provider_list': [PROVIDER_ROW]}
        self.assertEqual(_extract_rows(payload, 'providers', 'provider_data', 'employees', 'staff', 'data', 'result'), [PROVIDER_ROW])

    def test_finds_rows_nested_two_levels_deep(self):
        payload = {'status': 'ok', 'result': {'meta': {}, 'items': [PROVIDER_ROW]}}
        self.assertEqual(_extract_rows(payload, 'providers'), [PROVIDER_ROW])

    def test_returns_empty_list_when_nothing_matches(self):
        payload = {'status': 'ok', 'message': 'no providers'}
        self.assertEqual(_extract_rows(payload, 'providers'), [])
