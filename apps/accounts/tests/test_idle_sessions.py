from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
import jwt
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.utils import timezone
from ninja.errors import HttpError
from redis.exceptions import ConnectionError as RedisConnectionError
from apps.accounts import api, auth
from apps.accounts.schemas import RefreshRequest
from apps.accounts.session_security import enforce_session_activity
from apps.tenants.api import _superadmin_organization_domain
from shared.middleware import _org_id_from_password_set_link


@override_settings(
    AUTH_SESSION_MAX_HOURS=8,
    JWT_SECRET_KEY='idle-session-test-signing-key-at-least-32-bytes',
    JWT_ALGORITHM='HS256',
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15,
    JWT_REFRESH_TOKEN_EXPIRE_DAYS=7,
)
class IdleSessionTests(SimpleTestCase):
    def setUp(self):
        self.now = timezone.now().timestamp()
        self.tenant = SimpleNamespace(pk=1, automatic_logout_enabled=True, automatic_logout_minutes=5)
        self.user = SimpleNamespace(
            pk=1, id=1, email='user@example.com', role='staff', is_superuser=False,
            organization_id=1, external_admin_id=None,
        )
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

    def test_user_token_cannot_be_issued_for_another_organization(self):
        self.user.organization_id = 2
        with self.assertRaises(HttpError) as error:
            api._issue_tokens(self.user, 1)
        self.assertEqual(error.exception.status_code, 401)

    def test_refresh_rejects_token_bound_to_another_organization(self):
        self.user.organization_id = 2
        token = auth.create_refresh_token(self.user, 1, session_started_at=self.now,
                                          session_id='session-one', idle_started_at=self.now)
        with mock.patch.object(api.User.objects, 'get', return_value=self.user):
            with self.assertRaises(HttpError) as error:
                api.refresh_token(SimpleNamespace(tenant=self.tenant), RefreshRequest(refresh_token=token))
        self.assertEqual(error.exception.status_code, 401)

    def test_access_token_rejects_user_from_another_organization(self):
        token = auth.create_access_token(self.user, 1, session_started_at=self.now)
        wrong_org_user = SimpleNamespace(
            id=1, is_superuser=False, organization_id=2, is_active=True,
        )
        with mock.patch.object(auth.User.objects, 'get', return_value=wrong_org_user), \
             mock.patch.object(auth, 'is_token_blocked', return_value=False), \
             mock.patch('apps.accounts.session_security.enforce_session_activity'):
            self.assertIsNone(auth.JWTAuth().authenticate(SimpleNamespace(tenant=self.tenant), token))

    def test_password_setup_token_routes_to_its_invited_organization(self):
        request = RequestFactory().post(
            '/api/v1/auth/password-link/set-password',
            data='{"token":"pwl_synthetic-token"}',
            content_type='application/json',
        )
        query = mock.Mock()
        query.values_list.return_value.first.return_value = 42
        with mock.patch('apps.accounts.models.PasswordSetLink.objects.filter', return_value=query) as lookup:
            self.assertEqual(_org_id_from_password_set_link(request), 42)
        self.assertEqual(lookup.call_args.kwargs['token_prefix'], 'pwl_synt')

    def test_password_setup_info_get_routes_by_invite_token(self):
        request = RequestFactory().get('/api/v1/auth/password-link/pwl_synthetic-token')
        query = mock.Mock()
        query.values_list.return_value.first.return_value = 42
        with mock.patch('apps.accounts.models.PasswordSetLink.objects.filter', return_value=query) as lookup:
            self.assertEqual(_org_id_from_password_set_link(request), 42)
        self.assertEqual(lookup.call_args.kwargs['token_prefix'], 'pwl_synt')

    def test_blank_superadmin_domain_uses_configured_app_host(self):
        with override_settings(FRONTEND_BASE_URL='https://app.example.test:3443'):
            self.assertEqual(_superadmin_organization_domain('', 'new-clinic'), 'app.example.test')
            self.assertEqual(_superadmin_organization_domain('custom.example.test', 'new-clinic'), 'custom.example.test')
