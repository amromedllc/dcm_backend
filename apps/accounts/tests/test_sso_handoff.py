import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse
from django.test import SimpleTestCase
from ninja.errors import HttpError
from redis.exceptions import ConnectionError
from apps.accounts import api
from apps.accounts.schemas import TokenResponse

class SsoHandoffTests(SimpleTestCase):
    def setUp(self):
        self.values = {}
        self.lock = threading.Lock()
        self.redis = mock.Mock()
        self.redis.setex.side_effect = lambda key, ttl, value: self.values.__setitem__(key, value)
        def consume(key):
            with self.lock:
                return self.values.pop(key, None)
        self.redis.getdel.side_effect = consume
        patch = mock.patch('apps.accounts.auth._redis', return_value=self.redis)
        patch.start()
        self.addCleanup(patch.stop)
        self.tokens = TokenResponse(access_token='access-secret', refresh_token='refresh-secret',
            user_id=1, email='user@example.test', role='staff', full_name='User')
        self.request = SimpleNamespace(tenant=SimpleNamespace(pk=12))

    def code(self):
        response = api._sso_success_redirect(self.tokens, '/clients', 12)
        for secret in ('access-secret', 'refresh-secret', 'user@example.test'):
            self.assertNotIn(secret, response.url)
        self.assertEqual(self.redis.setex.call_args.args[1], 60)
        return parse_qs(urlparse(response.url).fragment)['code'][0]

    def test_handoff_returns_original_session_once(self):
        code = self.code()
        data = api.exchange_sso_handoff(self.request, {'code': code})
        self.assertEqual(data['access_token'], 'access-secret')
        self.assertEqual(data['redirect_target'], '/clients')
        with self.assertRaises(HttpError) as error:
            api.exchange_sso_handoff(self.request, {'code': code})
        self.assertEqual(error.exception.status_code, 401)

    def test_only_one_concurrent_exchange_succeeds(self):
        code = self.code()
        def exchange(_):
            try:
                api.exchange_sso_handoff(self.request, {'code': code})
                return True
            except HttpError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(exchange, range(8))), 1)

    def test_facility_mismatch_cannot_receive_tokens(self):
        code = self.code()
        with self.assertRaises(HttpError):
            api.exchange_sso_handoff(SimpleNamespace(tenant=SimpleNamespace(pk=99)), {'code': code})

    def test_missing_expired_or_malformed_code_is_rejected(self):
        for value in ('', 'short', 'a' * 43, None, []):
            with self.assertRaises(HttpError) as error:
                api.exchange_sso_handoff(self.request, {'code': value})
            self.assertEqual(error.exception.status_code, 401)

    def test_unavailable_handoff_storage_fails_closed(self):
        self.redis.setex.side_effect = ConnectionError('offline')
        with self.assertRaises(HttpError) as error:
            api._sso_success_redirect(self.tokens, '/', 12)
        self.assertEqual(error.exception.status_code, 503)
