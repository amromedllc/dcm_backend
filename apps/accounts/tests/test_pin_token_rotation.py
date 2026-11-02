from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import jwt
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from ninja.errors import HttpError
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.accounts import api, auth
from apps.accounts.schemas import PinUnlockRequest, RefreshRequest


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expiries = {}

    def exists(self, key):
        return key in self.values

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, *, nx, ex):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expiries[key] = ex
        return True


@override_settings(AUTH_SESSION_MAX_HOURS=8, JWT_SECRET_KEY='pin-rotation-test-signing-key-at-least-32-bytes')
class PinTokenRotationTests(SimpleTestCase):
    def setUp(self):
        audit = mock.patch('apps.accounts.security_audit.record_security_event')
        audit.start()
        self.addCleanup(audit.stop)
        self.now = timezone.now()
        self.start = (self.now - timedelta(hours=2)).timestamp()
        self.user = mock.Mock(id=1, email='test@example.com', role='staff', is_superuser=False,
                              pin_lock_enabled=True, has_pin=True, pin_locked_until=None, pin_failed_attempts=0)
        self.user.check_pin.return_value = True
        self.request = SimpleNamespace(tenant=SimpleNamespace(pk=1, pin_lock_allowed=True))
        self.redis = FakeRedis()
        org = mock.Mock()
        org.only.return_value.first.return_value = SimpleNamespace(automatic_logout_enabled=True, automatic_logout_minutes=5)
        locked = mock.Mock()
        locked.get.return_value = self.user
        for patch in (mock.patch('apps.accounts.pin_security.transaction.atomic'),
                      mock.patch.object(api.User.objects, 'select_for_update', return_value=locked),mock.patch.object(auth, '_redis', return_value=self.redis),
                      mock.patch.object(api.User.objects, 'get', return_value=self.user),
                      mock.patch('apps.tenants.models.Organization.objects.filter', return_value=org)):
            patch.start()
            self.addCleanup(patch.stop)

    def token(self, expired=False, include_jti=True):
        payload = dict(sub='1', org_id=1, type='refresh', iat=self.start, session_started_at=self.start,
                       exp=(self.now + timedelta(seconds=-60 if expired else 300)).timestamp())
        if include_jti:
            payload['jti'] = 'original-refresh'
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def unlock(self, token):
        return api.unlock_with_pin(self.request, PinUnlockRequest(pin='1234', refresh_token=token))

    def test_success_consumes_old_token_and_new_token_can_unlock(self):
        old = self.token()
        result = self.unlock(old)
        payload = auth.decode_token(result.refresh_token)
        self.assertNotEqual(payload['jti'], 'original-refresh')
        self.assertEqual(payload['session_started_at'], self.start)
        with self.assertRaises(HttpError) as error:
            self.unlock(old)
        self.assertEqual(error.exception.status_code, 401)
        self.assertTrue(self.unlock(result.refresh_token).access_token)

    def test_expired_token_revocation_lasts_until_absolute_session_deadline(self):
        token = self.token(expired=True)
        self.unlock(token)
        ttl = self.redis.expiries[auth._blocklist_key('original-refresh')]
        self.assertGreater(ttl, 5 * 3600)
        self.assertLessEqual(ttl, 6 * 3600)
        with self.assertRaises(HttpError):
            self.unlock(token)

    def test_consumed_refresh_also_rejected_by_normal_refresh(self):
        token = self.token()
        self.unlock(token)
        with self.assertRaises(HttpError) as error:
            api.refresh_token(self.request, RefreshRequest(refresh_token=token))
        self.assertEqual(error.exception.status_code, 401)

    def test_wrong_pin_does_not_consume_refresh_token(self):
        token = self.token()
        self.user.check_pin.return_value = False
        with self.assertRaises(HttpError) as error:
            self.unlock(token)
        self.assertEqual(error.exception.status_code, 400)
        self.assertFalse(self.redis.values)
        self.user.check_pin.return_value = True
        self.assertTrue(self.unlock(token).refresh_token)

    def test_missing_identity_is_rejected(self):
        with self.assertRaises(HttpError) as error:
            self.unlock(self.token(include_jti=False))
        self.assertEqual(error.exception.status_code, 401)

    def test_race_loser_does_not_receive_replacement_tokens(self):
        with mock.patch.object(api, 'consume_refresh_token', return_value=False):
            with self.assertRaises(HttpError) as error:
                self.unlock(self.token())
        self.assertEqual(error.exception.status_code, 401)

    def test_redis_outage_fails_closed(self):
        with mock.patch.object(api, 'consume_refresh_token', side_effect=RedisConnectionError('offline')), \
             self.assertLogs('apps.accounts.api', level='ERROR'):
            with self.assertRaises(HttpError) as error:
                self.unlock(self.token())
        self.assertEqual(error.exception.status_code, 503)
