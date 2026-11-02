from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
import jwt
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from ninja.errors import HttpError
from redis.exceptions import ConnectionError as RedisConnectionError
from apps.accounts import api, auth
from apps.accounts.schemas import RefreshRequest
from apps.accounts.session_security import enforce_session_activity


@override_settings(AUTH_SESSION_MAX_HOURS=8, JWT_SECRET_KEY='idle-session-test-signing-key-at-least-32-bytes')
class IdleSessionTests(SimpleTestCase):
    def setUp(self):
        self.now = timezone.now().timestamp()
        self.tenant = SimpleNamespace(pk=1, automatic_logout_enabled=True, automatic_logout_minutes=5)
        self.user = SimpleNamespace(pk=1, id=1, email='user@example.com', role='staff', is_superuser=False)
        self.payload = dict(sub='1', org_id=1, type='access', sid='session-one',
                            session_started_at=self.now, idle_started_at=self.now,
                            iat=self.now, exp=self.now+900, jti='test-access')
        self.redis = mock.Mock()
        self.redis.exists.return_value = False
        self.redis.get.return_value = None
        self.redis.eval.return_value = 1
        patch = mock.patch.object(auth, '_redis', return_value=self.redis)
        patch.start()
        self.addCleanup(patch.stop)

    def test_protected_api_is_denied_when_server_session_is_idle(self):
        from django.conf import settings
        token = jwt.encode(self.payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        self.redis.eval.return_value = 0
        with mock.patch.object(auth.User.objects, 'get') as lookup:
            with self.assertRaises(HttpError) as error:
                auth.JWTAuth().authenticate(SimpleNamespace(tenant=self.tenant), token)
            self.assertEqual(error.exception.status_code, 423)
            lookup.assert_not_called()

    def test_polling_checks_activity_without_touching_it(self):
        enforce_session_activity(self.payload, self.tenant)
        # Lua argument 4 is touch: normal requests never move the deadline.
        self.assertEqual(self.redis.eval.call_args.args[6], 0)
        enforce_session_activity(self.payload, self.tenant, touch=True)
        self.assertEqual(self.redis.eval.call_args.args[6], 1)

    def test_expired_heartbeat_is_rejected_instead_of_resurrecting_session(self):
        self.redis.eval.return_value = 0
        with self.assertRaises(HttpError) as error:
            enforce_session_activity(self.payload, self.tenant, touch=True)
        self.assertEqual(error.exception.status_code, 423)

    def test_disabled_automatic_logout_does_not_access_idle_store(self):
        self.tenant.automatic_logout_enabled = False
        enforce_session_activity(self.payload, self.tenant)
        self.redis.eval.assert_not_called()

    def test_redis_failure_fails_closed(self):
        self.redis.eval.side_effect = RedisConnectionError('offline')
        with self.assertRaises(HttpError) as error:
            enforce_session_activity(self.payload, self.tenant)
        self.assertEqual(error.exception.status_code, 503)

    def test_refresh_preserves_session_identity_and_idle_clock(self):
        token = auth.create_refresh_token(self.user, 1, session_started_at=self.now,
                                          session_id='session-one', idle_started_at=self.now)
        request = SimpleNamespace(tenant=self.tenant)
        with mock.patch.object(api.User.objects, 'get', return_value=self.user):
            result = api.refresh_token(request, RefreshRequest(refresh_token=token))
        access = auth.decode_token(result.access_token)
        self.assertEqual(access['sid'], 'session-one')
        self.assertEqual(access['idle_started_at'], self.now)
        self.assertEqual(self.redis.eval.call_args.args[6], 0)

    def test_normal_refresh_rejects_idle_session(self):
        self.redis.eval.return_value = 0
        token = auth.create_refresh_token(self.user, 1, session_started_at=self.now,
                                          session_id='session-one', idle_started_at=self.now)
        with self.assertRaises(HttpError) as error:
            api.refresh_token(SimpleNamespace(tenant=self.tenant), RefreshRequest(refresh_token=token))
        self.assertEqual(error.exception.status_code, 423)

    def test_new_sessions_share_identity_between_access_and_refresh(self):
        user = SimpleNamespace(**vars(self.user), full_name='Test User')
        with mock.patch.object(api.User.objects, 'filter'):
            result = api._issue_tokens(user, 1)
        access, refresh = auth.decode_token(result.access_token), auth.decode_token(result.refresh_token)
        self.assertEqual(access['sid'], refresh['sid'])
        self.assertEqual(access['idle_started_at'], refresh['idle_started_at'])

    def test_missing_idle_identity_requires_new_sign_in(self):
        del self.payload['sid']
        with self.assertRaises(jwt.InvalidTokenError):
            enforce_session_activity(self.payload, self.tenant)
