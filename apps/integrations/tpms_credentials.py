"""Encrypt / decrypt TherapyPMS integration credentials stored on Organization.

Uses Fernet derived from SECRET_KEY (same pattern as accounts.mfa), with a
distinct purpose string so MFA secrets and integration passwords cannot be
interchanged.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from ninja.errors import HttpError


def _fernet() -> Fernet:
    digest = hashlib.sha256(f'{settings.SECRET_KEY}:tpms-integration'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_password(password: str) -> str:
    return _fernet().encrypt(password.encode()).decode()


def decrypt_password(token: str) -> str:
    if not token:
        raise HttpError(400, 'TherapyPMS credentials are missing. Please reconnect.')
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise HttpError(500, 'TherapyPMS credentials could not be read. Please reconnect.') from exc


def _ssn_fernet() -> Fernet:
    # Distinct purpose string — a provider's SSN must not decrypt under the
    # same key as the org's TherapyPMS login password, or vice versa.
    digest = hashlib.sha256(f'{settings.SECRET_KEY}:tpms-provider-ssn'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_ssn(ssn: str) -> str:
    return _ssn_fernet().encrypt(ssn.encode()).decode()


def decrypt_ssn(token: str) -> str:
    """Best-effort decrypt for comparison/display — returns '' rather than
    raising, since a sync loop shouldn't crash over one unreadable SSN."""
    if not token:
        return ''
    try:
        return _ssn_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ''
