from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

from django.db import connection, close_old_connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts import api
from apps.accounts.models import User
from apps.accounts.pin_security import verify_pin_attempt, set_account_pin
from apps.accounts.schemas import VerifyPinRequest


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PinAttemptTests(TransactionTestCase):
    def setUp(self):
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema_to_public)
        self.user = User.objects.create_user(email='pin-attempt@example.com', password='account-password')
        self.user.set_pin('1234')
        self.user.save()

    def reject(self, action, status):
        with self.assertRaises(HttpError) as error:
            action()
        self.assertEqual(error.exception.status_code, status)

    def test_concurrent_failures_lock_after_five_and_are_committed(self):
        user_id = self.user.pk
        def attempt(_):
            close_old_connections()
            try:
                verify_pin_attempt(user_id, '9999')
            except HttpError as error:
                return error.status_code
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(attempt, range(8)))
        self.assertEqual(statuses.count(400), 4)
        self.assertEqual(statuses.count(429), 4)
        self.user.refresh_from_db()
        self.assertGreater(self.user.pin_locked_until, timezone.now())
        self.reject(lambda: verify_pin_attempt(user_id, '1234'), 429)

    def test_verify_and_unlock_share_attempt_budget(self):
        for _ in range(3):
            self.reject(lambda: api.verify_my_pin(SimpleNamespace(user=self.user), VerifyPinRequest(pin='9999')), 400)
        self.reject(lambda: verify_pin_attempt(self.user.pk, '9999', require_enabled=True), 400)
        self.reject(lambda: verify_pin_attempt(self.user.pk, '9999', require_enabled=True), 429)
        self.reject(lambda: api.verify_my_pin(SimpleNamespace(user=self.user), VerifyPinRequest(pin='1234')), 429)

    def test_success_and_expired_lock_reset_attempts(self):
        self.reject(lambda: verify_pin_attempt(self.user.pk, '9999'), 400)
        verify_pin_attempt(self.user.pk, '1234')
        self.user.refresh_from_db()
        self.assertEqual(self.user.pin_failed_attempts, 0)
        self.user.pin_locked_until = timezone.now() - timedelta(seconds=1)
        self.user.save()
        verify_pin_attempt(self.user.pk, '1234')
        self.user.refresh_from_db()
        self.assertIsNone(self.user.pin_locked_until)

    def test_change_cannot_omit_current_pin_and_failures_are_saved(self):
        self.reject(lambda: set_account_pin(self.user.pk, '5678'), 403)
        self.assertTrue(User.objects.get(pk=self.user.pk).check_pin('1234'))
        self.reject(lambda: set_account_pin(self.user.pk, '5678', current_pin='9999'), 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pin_failed_attempts, 1)
        set_account_pin(self.user.pk, '5678', current_pin='1234')
        self.assertTrue(User.objects.get(pk=self.user.pk).check_pin('5678'))

    def test_recovery_requires_password_and_is_rate_limited(self):
        for _ in range(4):
            self.reject(lambda: set_account_pin(self.user.pk, '5678', password='wrong'), 400)
        self.reject(lambda: set_account_pin(self.user.pk, '5678', password='wrong'), 429)
        self.reject(lambda: set_account_pin(self.user.pk, '5678', password='account-password'), 429)
        self.user.pin_locked_until = timezone.now() - timedelta(seconds=1)
        self.user.save(update_fields=['pin_locked_until'])
        set_account_pin(self.user.pk, '5678', password='account-password')
        self.assertTrue(User.objects.get(pk=self.user.pk).check_pin('5678'))

    def test_disabled_pin_cannot_unlock(self):
        self.user.pin_lock_enabled = False
        self.user.save()
        self.reject(lambda: verify_pin_attempt(self.user.pk, '1234', require_enabled=True), 400)

    def test_disabling_and_removal_require_proof_and_leave_pin_on_failure(self):
        from apps.accounts.pin_security import update_pin_protection
        for remove in (False, True):
            self.reject(lambda: update_pin_protection(self.user.pk, remove=remove), 403)
            self.reject(lambda: update_pin_protection(self.user.pk, remove=remove, current_pin='9999'), 400)
            self.user.refresh_from_db()
            self.assertTrue(self.user.pin_lock_enabled)
            self.assertTrue(self.user.check_pin('1234'))
        self.assertEqual(self.user.pin_failed_attempts, 2)

    def test_disable_keeps_pin_and_reenable_does_not_require_proof(self):
        from apps.accounts.pin_security import update_pin_protection
        user = update_pin_protection(self.user.pk, current_pin='1234')
        self.assertFalse(user.pin_lock_enabled)
        self.assertTrue(user.check_pin('1234'))
        user = update_pin_protection(self.user.pk, enabled=True)
        self.assertTrue(user.pin_lock_enabled)

    def test_password_removal_clears_pin_and_enable_requires_setup(self):
        from apps.accounts.pin_security import update_pin_protection
        user = update_pin_protection(self.user.pk, remove=True, password='account-password')
        self.assertFalse(user.has_pin)
        self.assertFalse(user.pin_lock_enabled)
        self.assertIsNone(user.pin_length)
        self.assertEqual(user.pin_failed_attempts, 0)
        self.reject(lambda: update_pin_protection(self.user.pk, enabled=True), 400)

    def test_protection_changes_share_lockout_and_cannot_bypass_with_password(self):
        from apps.accounts.pin_security import update_pin_protection
        for _ in range(4):
            self.reject(lambda: verify_pin_attempt(self.user.pk, '9999'), 400)
        self.reject(lambda: update_pin_protection(self.user.pk, remove=True, password='wrong'), 429)
        self.reject(lambda: update_pin_protection(self.user.pk, current_pin='1234'), 429)
        self.reject(lambda: update_pin_protection(self.user.pk, remove=True, password='account-password'), 429)
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_pin)
        self.assertTrue(self.user.pin_lock_enabled)
