from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
import jwt
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from ninja.errors import HttpError
from apps.accounts import api, auth
from apps.accounts.schemas import PinUnlockRequest, RefreshRequest


@override_settings(AUTH_SESSION_MAX_HOURS=8)
class SessionLifetimeTests(SimpleTestCase):
    def setUp(self):
        audit = mock.patch('apps.accounts.security_audit.record_security_event')
        audit.start()
        self.addCleanup(audit.stop)
        self.now = timezone.now()
        self.user = mock.Mock(id=1, email='test@example.com', role='staff', is_superuser=False,
                              pin_lock_enabled=True, has_pin=True, pin_locked_until=None)
        self.user.check_pin.return_value = True
        self.request = SimpleNamespace(tenant=SimpleNamespace(pk=1, pin_lock_allowed=True))

    def token(self, start, anchor=True, expired=True):
        payload = dict(sub='1', org_id=1, type='refresh', jti='test', iat=start,
                       exp=(self.now + timedelta(seconds=-30 if expired else 60)).timestamp())
        if anchor:
            payload['session_started_at'] = start
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def unlock(self, token):
        org = mock.Mock()
        org.only.return_value.first.return_value = SimpleNamespace(automatic_logout_enabled=True, automatic_logout_minutes=5)
        with mock.patch('apps.accounts.pin_security.transaction.atomic'), \
             mock.patch.object(api.User.objects, 'select_for_update') as locked, \
             mock.patch.object(api.User.objects, 'get', return_value=self.user), \
             mock.patch.object(api, 'is_token_blocked', return_value=False), \
             mock.patch.object(api, 'consume_refresh_token', return_value=True), \
             mock.patch('apps.tenants.models.Organization.objects.filter', return_value=org):
            locked.return_value.get.return_value = self.user
            return api.unlock_with_pin(self.request, PinUnlockRequest(pin='1234', refresh_token=token))

    def test_inactivity_expired_token_can_unlock_inside_absolute_limit(self):
        start = (self.now - timedelta(hours=2)).timestamp()
        result = self.unlock(self.token(start))
        for token in (result.access_token, result.refresh_token):
            payload = auth.decode_token(token)
            self.assertEqual(payload['session_started_at'], start)
            self.assertLessEqual(payload['exp'], start + 8 * 3600)

    def test_repeated_unlock_does_not_restart_clock(self):
        start = (self.now - timedelta(hours=2)).timestamp()
        first = self.unlock(self.token(start))
        second = self.unlock(first.refresh_token)
        self.assertEqual(auth.decode_token(second.refresh_token)['session_started_at'], start)
        self.assertNotEqual(first.refresh_token, second.refresh_token)

    def test_legacy_token_uses_signed_original_issuance_time(self):
        start = (self.now - timedelta(hours=1)).timestamp()
        result = self.unlock(self.token(start, anchor=False))
        self.assertEqual(auth.decode_token(result.refresh_token)['session_started_at'], start)

    def test_absolute_expiry_rejects_pin_before_user_lookup(self):
        start = (self.now - timedelta(hours=8, seconds=1)).timestamp()
        with mock.patch.object(api.User.objects, 'get') as lookup:
            with self.assertRaises(HttpError) as error:
                api.unlock_with_pin(self.request, PinUnlockRequest(pin='1234', refresh_token=self.token(start)))
            self.assertEqual(error.exception.status_code, 401)
            lookup.assert_not_called()

    def test_normal_refresh_cannot_bypass_limit(self):
        start = (self.now - timedelta(hours=9)).timestamp()
        with mock.patch.object(api.User.objects, 'get', return_value=self.user), \
             mock.patch.object(api, 'is_token_blocked', return_value=False):
            with self.assertRaises(HttpError) as error:
                api.refresh_token(self.request, RefreshRequest(refresh_token=self.token(start, expired=False)))
            self.assertEqual(error.exception.status_code, 401)

    def test_access_token_cannot_outlive_deadline(self):
        start = (self.now - timedelta(hours=7, minutes=59)).timestamp()
        payload = auth.decode_token(auth.create_access_token(self.user, 1, session_started_at=start))
        self.assertEqual(payload['exp'], start + 8 * 3600)

    def test_access_authentication_rejects_session_past_absolute_limit(self):
        start = (self.now - timedelta(hours=9)).timestamp()
        payload = dict(sub='1', org_id=1, type='access', iat=start,
                       session_started_at=start, exp=(self.now + timedelta(minutes=5)).timestamp())
        token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        with mock.patch.object(auth.User.objects, 'get') as lookup:
            self.assertIsNone(auth.JWTAuth().authenticate(self.request, token))
            lookup.assert_not_called()

    def test_invalid_session_start_is_rejected(self):
        for payload in ({}, {'iat': None}, {'iat': float('nan')}, {'iat': True}, {'iat': self.now.timestamp()+3600}):
            with self.assertRaises(jwt.InvalidTokenError):
                auth.session_start_from_payload(payload)
