"""Authenticator-app (TOTP) multi-factor authentication helpers.

Passwords are still verified by TherapyPMS at login; MFA is a second step that
runs after that succeeds (see accounts.api.login). Until the code is verified,
the client only holds a short-lived `mfa` token, never access/refresh tokens.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import time
from datetime import timedelta
from typing import Any

import jwt
import pyotp
import qrcode
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.utils import timezone
from ninja.errors import HttpError

from .models import User, UserMFA

ISSUER = 'Progressly'
TOTP_INTERVAL = 30
MFA_TOKEN_MINUTES = 5
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 5


def _fernet() -> Fernet:
    digest = hashlib.sha256(f'{settings.SECRET_KEY}:user-mfa'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise HttpError(500, 'MFA secret could not be read') from exc


def provisioning_uri(user: User, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)


def qr_data_uri(uri: str) -> str:
    buffer = io.BytesIO()
    qrcode.make(uri, box_size=6, border=2).save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


def start_setup(user: User) -> dict[str, str]:
    """(Re)start enrolment. Refuses if MFA is already active."""
    mfa = UserMFA.objects.filter(user=user).first()
    if mfa and mfa.is_active:
        raise HttpError(400, 'MFA is already set up for this account')
    secret = pyotp.random_base32()
    if mfa:
        mfa.secret_encrypted = encrypt_secret(secret)
        mfa.failed_attempts = 0
        mfa.locked_until = None
        mfa.last_used_step = None
        mfa.save()
    else:
        UserMFA.objects.create(user=user, secret_encrypted=encrypt_secret(secret))
    uri = provisioning_uri(user, secret)
    return {'secret': secret, 'otpauth_uri': uri, 'qr_code': qr_data_uri(uri)}


def check_code(user: User, code: str, *, activate: bool = False) -> None:
    """Validate a 6-digit code or raise HttpError. Locks the account's MFA
    after repeated failures and rejects re-use of an already-accepted code."""
    mfa = UserMFA.objects.filter(user=user).first()
    if mfa is None or (not mfa.is_active and not activate):
        raise HttpError(400, 'MFA is not set up for this account')

    now = timezone.now()
    if mfa.locked_until and mfa.locked_until > now:
        raise HttpError(429, 'Too many incorrect codes. Try again in a few minutes.')

    code = (code or '').strip().replace(' ', '')
    totp = pyotp.TOTP(decrypt_secret(mfa.secret_encrypted), interval=TOTP_INTERVAL)
    current_step = int(time.time() // TOTP_INTERVAL)
    matched_step = None
    for step in (current_step - 1, current_step, current_step + 1):
        if hmac.compare_digest(totp.generate_otp(step), code):
            matched_step = step
            break

    if matched_step is None or (mfa.last_used_step is not None and matched_step <= mfa.last_used_step):
        mfa.failed_attempts += 1
        if mfa.failed_attempts >= MAX_FAILED_ATTEMPTS:
            mfa.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            mfa.failed_attempts = 0
        mfa.save(update_fields=['failed_attempts', 'locked_until'])
        raise HttpError(400, 'Invalid verification code')

    mfa.failed_attempts = 0
    mfa.locked_until = None
    mfa.last_used_step = matched_step
    if activate and not mfa.is_active:
        mfa.is_active = True
        mfa.confirmed_at = now
    mfa.save()


def create_mfa_token(user: User, tenant_id: int) -> str:
    payload: dict[str, Any] = {
        'sub': str(user.id),
        'org_id': tenant_id,
        'type': 'mfa',
        'jti': secrets.token_hex(16),
        'iat': timezone.now().timestamp(),
        'exp': (timezone.now() + timedelta(minutes=MFA_TOKEN_MINUTES)).timestamp(),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def user_from_mfa_token(token: str, tenant_id: int | None) -> tuple[User, int]:
    """Resolve the user a login challenge belongs to, bound to the same tenant
    the password step happened against."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HttpError(401, 'Your sign-in expired. Please sign in again.') from exc
    if payload.get('type') != 'mfa':
        raise HttpError(401, 'Your sign-in expired. Please sign in again.')
    org_id = payload.get('org_id')
    if tenant_id is not None and org_id != tenant_id:
        raise HttpError(401, 'Your sign-in expired. Please sign in again.')
    try:
        user = User.objects.get(id=int(payload['sub']), is_active=True)
    except (User.DoesNotExist, KeyError, ValueError) as exc:
        raise HttpError(401, 'Your sign-in expired. Please sign in again.') from exc
    return user, org_id


def needs_mfa(user: User) -> bool:
    return user.mfa_required or user.mfa_enabled
