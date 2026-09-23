"""Authenticator-app MFA: login gate, enrolment, verification, recovery."""
import time
from types import SimpleNamespace
from unittest import mock

import pyotp
from django.test import TestCase
from ninja.errors import HttpError

from apps.accounts import api as accounts_api
from apps.accounts import mfa
from apps.accounts.models import User, UserMFA
from apps.accounts.schemas import (
    MfaCodeRequest, MfaRemoveRequest, MfaTokenCodeRequest, MfaTokenRequest,
)
from apps.tenants.models import Organization


class MfaTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='MFA Org', slug='mfa-org', schema_name='mfa_org')
        self.user = User.objects.create_user(
            email='mfa-user@example.com', password='x', organization=self.org, role=User.Role.STAFF,
        )
        self.admin = User.objects.create_user(
            email='mfa-admin@example.com', password='x', organization=self.org, role=User.Role.ADMIN,
        )

    def _request(self, user=None):
        return SimpleNamespace(user=user, tenant=self.org)

    def _enrol(self):
        """Complete setup via the account endpoints and return the secret."""
        setup = accounts_api.start_my_mfa_setup(self._request(self.user))
        accounts_api.confirm_my_mfa_setup(
            self._request(self.user), MfaCodeRequest(code=pyotp.TOTP(setup['secret']).now()),
        )
        return setup['secret']

    def test_login_gate_passes_tokens_when_mfa_not_needed(self):
        tokens = accounts_api._issue_tokens(self.user, self.org.pk)
        self.assertIs(accounts_api._mfa_gate(self.org, tokens), tokens)

    def test_login_gate_requires_setup_when_admin_requires_mfa(self):
        self.user.mfa_required = True
        self.user.save()
        tokens = accounts_api._issue_tokens(self.user, self.org.pk)
        result = accounts_api._mfa_gate(self.org, tokens)
        self.assertTrue(result.mfa_required)
        self.assertTrue(result.mfa_setup_required)
        self.assertIsNone(result.access_token)
        self.assertTrue(result.mfa_token)

    def test_secret_is_encrypted_at_rest(self):
        setup = accounts_api.start_my_mfa_setup(self._request(self.user))
        stored = UserMFA.objects.get(user=self.user).secret_encrypted
        self.assertNotIn(setup['secret'], stored)
        self.assertTrue(setup['qr_code'].startswith('data:image/png;base64,'))

    def test_full_login_setup_then_verify_flow(self):
        self.user.mfa_required = True
        self.user.save()
        challenge = accounts_api._mfa_gate(self.org, accounts_api._issue_tokens(self.user, self.org.pk))

        setup = accounts_api.mfa_login_setup_start(
            self._request(), MfaTokenRequest(mfa_token=challenge.mfa_token),
        )
        tokens = accounts_api.mfa_login_setup_confirm(
            self._request(),
            MfaTokenCodeRequest(mfa_token=challenge.mfa_token, code=pyotp.TOTP(setup['secret']).now()),
        )
        self.assertTrue(tokens.access_token)
        self.assertTrue(User.objects.get(pk=self.user.pk).mfa_enabled)

        # next login: challenge asks for a code, not setup
        next_challenge = accounts_api._mfa_gate(self.org, accounts_api._issue_tokens(self.user, self.org.pk))
        self.assertFalse(next_challenge.mfa_setup_required)

    def test_verify_rejects_wrong_code_and_replayed_code(self):
        secret = self._enrol()
        challenge = accounts_api._mfa_gate(self.org, accounts_api._issue_tokens(User.objects.get(pk=self.user.pk), self.org.pk))
        with self.assertRaises(HttpError):
            accounts_api.mfa_verify(self._request(), MfaTokenCodeRequest(mfa_token=challenge.mfa_token, code='000000'))

        # the code used to confirm enrolment cannot be reused straight away
        code = pyotp.TOTP(secret).at(time.time() + mfa.TOTP_INTERVAL)
        tokens = accounts_api.mfa_verify(self._request(), MfaTokenCodeRequest(mfa_token=challenge.mfa_token, code=code))
        self.assertTrue(tokens.access_token)
        with self.assertRaises(HttpError):
            accounts_api.mfa_verify(self._request(), MfaTokenCodeRequest(mfa_token=challenge.mfa_token, code=code))

    def test_repeated_failures_lock_mfa(self):
        self._enrol()
        user = User.objects.get(pk=self.user.pk)
        for _ in range(mfa.MAX_FAILED_ATTEMPTS):
            with self.assertRaises(HttpError):
                mfa.check_code(user, '000000')
        with self.assertRaises(HttpError) as ctx:
            mfa.check_code(user, '000000')
        self.assertEqual(ctx.exception.status_code, 429)

    def test_access_token_is_not_a_valid_mfa_token(self):
        tokens = accounts_api._issue_tokens(self.user, self.org.pk)
        with self.assertRaises(HttpError):
            mfa.user_from_mfa_token(tokens.access_token, self.org.pk)

    def test_mfa_token_is_bound_to_tenant(self):
        token = mfa.create_mfa_token(self.user, self.org.pk)
        with self.assertRaises(HttpError):
            mfa.user_from_mfa_token(token, self.org.pk + 999)

    def test_login_setup_cannot_replace_an_active_device(self):
        self._enrol()
        token = mfa.create_mfa_token(User.objects.get(pk=self.user.pk), self.org.pk)
        with self.assertRaises(HttpError):
            accounts_api.mfa_login_setup_start(self._request(), MfaTokenRequest(mfa_token=token))

    def test_user_can_disable_unless_required(self):
        secret = self._enrol()
        code = pyotp.TOTP(secret).at(time.time() + mfa.TOTP_INTERVAL)
        accounts_api.disable_my_mfa(self._request(self.user), MfaCodeRequest(code=code))
        self.assertFalse(UserMFA.objects.filter(user=self.user).exists())

        self.user.mfa_required = True
        self.user.save()
        with self.assertRaises(HttpError):
            accounts_api.disable_my_mfa(self._request(self.user), MfaCodeRequest(code='123456'))

    def test_admin_can_remove_mfa_with_password(self):
        self._enrol()
        request = self._request(self.admin)
        with mock.patch.object(accounts_api, '_verify_actor_password', return_value=False):
            with self.assertRaises(HttpError) as ctx:
                accounts_api.remove_user_mfa(request, self.user.id, MfaRemoveRequest(password='bad'))
            self.assertEqual(ctx.exception.status_code, 403)
        with mock.patch.object(accounts_api, 'require_permission'), \
                mock.patch.object(accounts_api, '_verify_actor_password', return_value=True):
            accounts_api.remove_user_mfa(request, self.user.id, MfaRemoveRequest(password='ok'))
        self.assertFalse(UserMFA.objects.filter(user=self.user).exists())
