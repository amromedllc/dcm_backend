import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
import pyotp
from django.db import connection, close_old_connections
from django.test import TransactionTestCase, override_settings
from ninja.errors import HttpError
from apps.accounts import mfa
from apps.accounts.models import User, UserMFA


@override_settings(SECRET_KEY='synthetic-mfa-concurrency-test-key', PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MfaConcurrencyTests(TransactionTestCase):
    def setUp(self):
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema_to_public)
        self.user = User.objects.create_user(email='mfa-concurrency@example.test', password='test-password')
        self.secret = pyotp.random_base32()
        self.factor = UserMFA.objects.create(user=self.user, secret_encrypted=mfa.encrypt_secret(self.secret), is_active=True)

    def test_same_code_is_accepted_only_once_under_concurrency(self):
        now = time.time()
        code = pyotp.TOTP(self.secret).at(now)
        def attempt(_):
            close_old_connections()
            try:
                mfa.check_code(self.user, code)
                return True
            except HttpError:
                return False
            finally:
                connection.close()
        with mock.patch('apps.accounts.mfa.time.time', return_value=now), ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(attempt, range(4))), 1)
        self.factor.refresh_from_db()
        self.assertEqual(self.factor.failed_attempts, 3)

    def test_failed_codes_are_committed_and_lock_after_five(self):
        now = time.time()
        valid = {pyotp.TOTP(self.secret).at(now + step * 30) for step in (-1, 0, 1)}
        wrong = next(str(i).zfill(6) for i in range(10) if str(i).zfill(6) not in valid)
        for _ in range(5):
            with self.assertRaises(HttpError): mfa.check_code(self.user, wrong)
        self.factor.refresh_from_db()
        self.assertIsNotNone(self.factor.locked_until)
        with self.assertRaises(HttpError) as error: mfa.check_code(self.user, pyotp.TOTP(self.secret).now())
        self.assertEqual(error.exception.status_code, 429)
