"""Exercise the supplied wire contract over local HTTP with synthetic identity/secret.

Fixtures follow the response format supplied by the user; no live TPMS calls.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

import jwt
import requests
from django.test import SimpleTestCase, override_settings

from apps.integrations.assigned_notes_client import AssignedNotesError, fetch_assigned_session_notes

SECRET = 'synthetic-progressly-shared-secret-for-tests-only'


class Handler(BaseHTTPRequestHandler):
    calls = []
    accept_headers = []
    response_status = 200
    response_body = b'{"status":"success","source_type":"old_form_builder","is_diagnostic":"No","notes":[]}'

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).calls.append((self.path, body))
        type(self).accept_headers.append(self.headers.get('Accept'))
        self.send_response(type(self).response_status)
        self.send_header('Content-Type', 'application/json')
        if self.response_status == 302:
            self.send_header('Location', '/redirect-must-not-receive-token')
        self.end_headers()
        self.wfile.write(type(self).response_body)


class AssignedNotesClientTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        super().tearDownClass()

    def setUp(self):
        Handler.calls = []
        Handler.accept_headers = []
        Handler.response_status = 200
        Handler.response_body = b'{"status":"success","source_type":"old_form_builder","is_diagnostic":"No","notes":[]}'
        self.settings = override_settings(
            TPMS_API_BASE_URL=f'http://127.0.0.1:{self.server.server_port}',
            TPMS_API_TIMEOUT_SECONDS=2,
            TPMS_PROGRESSLY_SHARED_SECRET=SECRET,
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def fetch(self, **kwargs):
        return fetch_assigned_session_notes(
            SimpleNamespace(external_employee_id=501), **kwargs,
        )

    def test_custom_page_body_and_hs256_claims_with_unique_token_per_call(self):
        self.assertEqual(self.fetch(), [])
        self.fetch()
        self.assertEqual(Handler.accept_headers, ['application/json', 'application/json'])
        decoded = []
        for path, body in Handler.calls:
            self.assertEqual(path, '/api/v1/ios/progressly/sn-assigned-list')
            self.assertEqual(set(body), {'token', 'session_id', 'source_type', 'is_diagnostic'})
            self.assertEqual(body['session_id'], 0)
            self.assertEqual(body['source_type'], 'old_form_builder')
            self.assertEqual(body['is_diagnostic'], 'Yes')
            self.assertEqual(jwt.get_unverified_header(body['token'])['alg'], 'HS256')
            claims = jwt.decode(body['token'], SECRET, algorithms=['HS256'], audience='tpms-progressly')
            self.assertEqual(claims['tpms_user_id'], 501)
            self.assertEqual(claims['exp'] - claims['iat'], 300)
            self.assertTrue(claims['jti'])
            decoded.append(claims)
        self.assertEqual(len(decoded), 2)
        self.assertNotEqual(decoded[0]['jti'], decoded[1]['jti'])

    def test_failed_or_redirected_requests_are_not_retried(self):
        for status in (302, 401, 409, 500):
            with self.subTest(status=status):
                Handler.calls = []
                Handler.response_status = status
                with self.assertRaises(AssignedNotesError) as error:
                    self.fetch()
                self.assertEqual(error.exception.status, 502)
                self.assertEqual(len(Handler.calls), 1)
                self.assertNotIn(SECRET, str(error.exception))

    def test_invalid_json_is_reported_without_response_content(self):
        Handler.response_body = b'synthetic-private-upstream-error'
        with self.assertRaises(AssignedNotesError) as error:
            self.fetch()
        self.assertNotIn('synthetic-private-upstream-error', str(error.exception))

    def test_transport_timeout_is_reported_without_request_secrets(self):
        with mock.patch('apps.integrations.assigned_notes_client.requests.post', side_effect=requests.Timeout):
            with self.assertRaises(AssignedNotesError) as error:
                self.fetch()
        self.assertEqual(error.exception.status, 502)
        self.assertNotIn(SECRET, str(error.exception))

    def test_missing_employee_or_secret_is_rejected_before_http(self):
        for employee, secret, expected in ((None, SECRET, 403), (501, '', 503)):
            with self.subTest(employee=employee):
                with override_settings(TPMS_PROGRESSLY_SHARED_SECRET=secret), self.assertRaises(AssignedNotesError) as error:
                    fetch_assigned_session_notes(SimpleNamespace(external_employee_id=employee))
                self.assertEqual(error.exception.status, expected)
        self.assertEqual(Handler.calls, [])

    def test_success_maps_only_documented_note_fields(self):
        Handler.response_body = json.dumps({
            'status': 'success', 'message': 'get session notes assigned list',
            'source_type': 'old_form_builder', 'is_diagnostic': 'No',
            'token': 'synthetic-upstream-token-not-for-browser',
            'notes': [{'id': 123, 'name': 'Session Note Template',
                       'url': 'https://forms.example.test/open/123', 'private_extra': 'not-for-browser'}],
        }).encode()
        self.assertEqual(self.fetch(), [{'id': 123, 'name': 'Session Note Template', 'url': 'https://forms.example.test/open/123'}])

    def test_invalid_envelopes_and_rows_are_rejected_without_leaking_payloads(self):
        for payload in (
            {'status': 'error', 'message': 'synthetic-private-error', 'notes': []},
            {'status': 'success', 'source_type': 'new_form_builder', 'notes': []},
            {'status': 'success', 'source_type': 'old_form_builder', 'notes': None},
            {'status': 'success', 'source_type': 'old_form_builder', 'notes': [{'id': True, 'name': 'Synthetic', 'url': 'https://forms.example.test/1'}]},
        ):
            with self.subTest(payload=payload):
                Handler.response_body = json.dumps(payload).encode()
                with self.assertRaises(AssignedNotesError) as error:
                    self.fetch()
                self.assertNotIn('synthetic-private-error', str(error.exception))

    def test_unsafe_opening_urls_are_rejected(self):
        for url in ('javascript:alert(1)', 'data:text/html,synthetic', '//forms.example.test/1',
                    'http://forms.example.test/1', 'https://user:password@forms.example.test/1'):
            with self.subTest(url=url):
                Handler.response_body = json.dumps({'status': 'success', 'source_type': 'old_form_builder',
                    'notes': [{'id': 123, 'name': 'Synthetic', 'url': url}]}).encode()
                with self.assertRaises(AssignedNotesError):
                    self.fetch()

    def test_http_failures_show_safe_status_and_action_without_upstream_body(self):
        cases = {401: 'authorization', 403: 'authorization', 422: 'request was rejected',
                 404: 'API address', 405: 'API address', 302: 'API address',
                 409: 'single-use', 429: 'Too many requests', 500: 'service is unavailable'}
        Handler.response_body = b'{"message":"synthetic-private-body-do-not-expose"}'
        for status, explanation in cases.items():
            with self.subTest(status=status):
                Handler.response_status = status
                with self.assertLogs('apps.integrations.assigned_notes_client', level='WARNING') as logs:
                    with self.assertRaises(AssignedNotesError) as error:
                        self.fetch()
                self.assertEqual(error.exception.status, 502)
                self.assertIn(f'HTTP {status}', str(error.exception))
                self.assertIn(explanation, str(error.exception))
                self.assertIn(f'status={status}', logs.output[0])
                for output in (str(error.exception), str(logs.output)):
                    self.assertNotIn('synthetic-private-body-do-not-expose', output)
                    self.assertNotIn(SECRET, output)

    def test_documented_provider_errors_are_recognized_without_echoing_other_fields(self):
        cases = {
            'Invalid or expired token': ('invalid_or_expired_token', 'services.progressly.sso_secret'),
            'Token has already been used': ('token_already_used', 'fresh signed token'),
            'Employee not found': ('employee_not_found', 'setting_api_type.data_collection = 4'),
        }
        Handler.response_status = 401
        for message, (reason, guidance) in cases.items():
            with self.subTest(message=message):
                Handler.response_body = json.dumps({'message': message,
                    'token': 'synthetic-token-private', 'employee': 'synthetic-person-private'}).encode()
                with self.assertLogs('apps.integrations.assigned_notes_client', level='WARNING') as logs:
                    with self.assertRaises(AssignedNotesError) as error:
                        self.fetch()
                self.assertIn(message, str(error.exception))
                self.assertIn(guidance, str(error.exception))
                self.assertIn(f'reason={reason}', logs.output[0])
                for output in (str(error.exception), str(logs.output)):
                    self.assertNotIn('synthetic-token-private', output)
                    self.assertNotIn('synthetic-person-private', output)
                    self.assertNotIn(SECRET, output)
                self.assertEqual(error.exception.status, 502)

    def test_refresh_after_consumed_token_uses_a_new_jwt(self):
        Handler.response_status = 401
        Handler.response_body = b'{"message":"Token has already been used"}'
        for _ in range(2):
            with self.assertRaises(AssignedNotesError):
                self.fetch()
        self.assertEqual(len(Handler.calls), 2)
        claims = [jwt.decode(body['token'], SECRET, algorithms=['HS256'], audience='tpms-progressly')
                  for _, body in Handler.calls]
        self.assertNotEqual(claims[0]['jti'], claims[1]['jti'])
