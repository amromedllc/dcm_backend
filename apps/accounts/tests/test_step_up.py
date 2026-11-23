import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone
from ninja.errors import HttpError
from redis.exceptions import ConnectionError

from apps.accounts import step_up
from apps.accounts.schemas import StepUpRequest


class GrantRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.lock = threading.Lock()
    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl
    def eval(self, script, count, key, *args):
        with self.lock:
            if script == step_up._RESERVE:
                value = int(self.values.get(key, 0))
                if value >= 5: return 0
                self.values[key] = value + 1
                return 1
            if script == step_up._CONSUME:
                if self.values.get(key) != args[0]: return 0
                del self.values[key]
                return 1
            if int(self.values.get(key, 0)) > 0:
                self.values[key] = int(self.values[key]) - 1
            return 0


class StepUpTests(SimpleTestCase):
    def setUp(self):
        self.user = mock.Mock(pk=7, password='hashed-not-plaintext', mfa_required=False, mfa_enabled=False, mfa=None)
        self.user.has_usable_password.return_value = True
        self.user.check_password.side_effect = lambda password: password == 'correct-password'
        self.body = b'{"role":"supervisor"}'
        self.request = SimpleNamespace(user=SimpleNamespace(pk=7), tenant=SimpleNamespace(pk=12),
            _jwt_payload={'sid': 'session-1', 'org_id': 12, 'session_started_at': timezone.now().timestamp()+1}, path='/api/v1/auth/users/42', method='PATCH', body=self.body, headers={})
        self.data = StepUpRequest(path='/auth/users/42', method='PATCH', digest=hashlib.sha256(self.body).hexdigest(), password='correct-password')
        self.redis = GrantRedis()
        for patch in (mock.patch.object(step_up, '_actor', return_value=(self.user, 'session-1', 12)),
                      mock.patch.object(step_up, '_redis', return_value=self.redis)):
            patch.start(); self.addCleanup(patch.stop)
    def grant(self):
        token = step_up.verify(self.request, self.data)['verification_token']
        self.request.headers['X-Step-Up-Token'] = token
        return token
    def rejected(self, callback, status):
        with self.assertRaises(HttpError) as error: callback()
        self.assertEqual(error.exception.status_code, status)

    def test_missing_proof_blocks_action_then_valid_grant_is_single_use(self):
        self.rejected(lambda: step_up.require_step_up(self.request), 428)
        token = self.grant()
        self.assertEqual(self.redis.ttls[f'dcm:step-up:grant:{token}'], 300)
        step_up.require_step_up(self.request)
        self.rejected(lambda: step_up.require_step_up(self.request), 428)

    def test_body_target_session_and_actor_mismatches_do_not_consume_grant(self):
        self.grant()
        original = self.request.body
        self.request.body = b'{"role":"admin"}'
        self.rejected(lambda: step_up.require_step_up(self.request), 428)
        self.request.body = original
        self.request.path = '/api/v1/auth/users/99'
        self.rejected(lambda: step_up.require_step_up(self.request), 428)
        self.request.path = '/api/v1/auth/users/42'
        for actor in ((self.user, 'different-session', 12), (self.user, 'session-1', 99)):
            with mock.patch.object(step_up, '_actor', return_value=actor):
                self.rejected(lambda: step_up.require_step_up(self.request), 428)
        step_up.require_step_up(self.request)

    def test_credential_change_invalidates_an_existing_grant(self):
        self.grant()
        self.user.password = 'new-password-hash'
        self.rejected(lambda: step_up.require_step_up(self.request), 428)

    def test_only_one_concurrent_consumer_succeeds(self):
        self.grant()
        def consume(_):
            try: step_up.require_step_up(self.request); return True
            except HttpError: return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(consume, range(8))), 1)

    def test_five_failures_lock_even_with_correct_password_afterward(self):
        self.data.password = 'wrong'
        for _ in range(5): self.rejected(lambda: step_up.verify(self.request, self.data), 403)
        self.data.password = 'correct-password'
        self.rejected(lambda: step_up.verify(self.request, self.data), 429)

    def test_success_does_not_use_up_failed_attempt_budget(self):
        for _ in range(8): self.grant()
        self.assertEqual(self.redis.values['dcm:step-up:attempts:12:7'], 0)

    def test_enrolled_mfa_requires_a_new_code_in_addition_to_password(self):
        self.user.mfa_enabled = True
        self.user.mfa = SimpleNamespace(is_active=True, method='totp', secret_encrypted='encrypted')
        with mock.patch.object(step_up.mfa, 'check_code', side_effect=HttpError(400, 'Invalid code')) as check:
            self.rejected(lambda: step_up.verify(self.request, self.data), 400)
            check.assert_called_once()
        self.assertFalse(any(key.startswith('dcm:step-up:grant:') for key in self.redis.values))

    def test_mfa_only_accounts_can_verify_but_accounts_without_factors_cannot(self):
        self.user.has_usable_password.return_value = False
        self.rejected(lambda: step_up.verify(self.request, self.data), 403)
        self.user.mfa_enabled = True
        self.user.mfa = SimpleNamespace(is_active=True, method='email', secret_encrypted=None, confirmed_at=timezone.now())
        with mock.patch.object(step_up.mfa, 'check_code') as check:
            self.grant(); check.assert_called_once()

    def test_storage_outage_fails_closed_and_unsupported_actions_are_rejected(self):
        with mock.patch.object(step_up, '_redis', side_effect=ConnectionError('offline')):
            self.rejected(lambda: step_up.verify(self.request, self.data), 503)
        self.data.path = '/auth/account/avatar'
        self.rejected(lambda: step_up.verify(self.request, self.data), 400)

    def test_expired_grant_and_required_unenrolled_mfa_are_rejected(self):
        token = self.grant()
        self.redis.values.pop(f'dcm:step-up:grant:{token}')
        self.rejected(lambda: step_up.require_step_up(self.request), 428)
        self.user.mfa_required = True
        self.rejected(lambda: step_up.verify(self.request, self.data), 403)

    def test_password_and_mfa_are_both_required_for_enrolled_local_accounts(self):
        self.user.mfa_enabled = True
        self.user.mfa = SimpleNamespace(is_active=True, method='totp', secret_encrypted='encrypted')
        self.data.code = '654321'
        self.data.password = 'wrong'
        with mock.patch.object(step_up.mfa, 'check_code') as check:
            self.rejected(lambda: step_up.verify(self.request, self.data), 403)
            check.assert_not_called()
        self.data.password = 'correct-password'
        with mock.patch.object(step_up.mfa, 'check_code') as check:
            self.grant()
            check.assert_called_once_with(self.user, '654321')
        step_up.require_step_up(self.request)

    def test_new_factor_requires_a_new_sign_in_for_mfa_only_accounts(self):
        from datetime import timedelta
        self.user.has_usable_password.return_value = False
        self.user.mfa_enabled = True
        self.user.mfa = SimpleNamespace(is_active=True, method='email', secret_encrypted=None,
                                       confirmed_at=timezone.now()+timedelta(minutes=1))
        self.rejected(lambda: step_up.verify(self.request, self.data), 403)
