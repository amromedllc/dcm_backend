"""Serialize PIN checks across requests and persist failures before returning errors."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from . import mfa
from .models import User
from .security_audit import record_security_event


_LOCKED = 'Too many incorrect attempts. Try again in a few minutes.'


def _check_locked_user(user, pin, *, password=False, request=None):
    """Called with a row lock. Return an error instead of rolling back failures."""
    now = timezone.now()
    if user.pin_locked_until and user.pin_locked_until > now:
        return HttpError(429, _LOCKED)
    valid = (user.has_usable_password() and user.check_password(pin)) if password else user.check_pin(pin.strip())
    if not valid:
        user.pin_failed_attempts += 1
        locked = user.pin_failed_attempts >= mfa.MAX_FAILED_ATTEMPTS
        if locked:
            user.pin_locked_until = now + timedelta(minutes=mfa.LOCKOUT_MINUTES)
            user.pin_failed_attempts = 0
        user.save(update_fields=['pin_failed_attempts', 'pin_locked_until'])
        if locked:
            record_security_event('pin.lockout', request=request, actor_id=user.pk,
                                  organization_id=user.organization_id, outcome='locked', status=429)
        return HttpError(429 if locked else 400, _LOCKED if locked else ('Incorrect password.' if password else 'Incorrect PIN.'))
    user.pin_failed_attempts = 0
    user.pin_locked_until = None
    user.save(update_fields=['pin_failed_attempts', 'pin_locked_until'])
    return None


def verify_pin_attempt(user_id, pin, *, require_enabled=False, request=None):
    with transaction.atomic():
        try:
            user = User.objects.select_for_update().get(pk=user_id, is_active=True)
        except User.DoesNotExist:
            raise HttpError(401, 'Session expired. Please sign in again.')
        if not user.has_pin or (require_enabled and not user.pin_lock_enabled):
            raise HttpError(400, 'No enabled PIN is available for this account.')
        error = _check_locked_user(user, pin, request=request)
    # Raising inside atomic() would undo the failed-attempt counter.
    if error:
        raise error
    return user


def set_account_pin(user_id, pin, *, current_pin=None, password=None, request=None):
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id, is_active=True)
        error = None
        if user.has_pin:
            if current_pin is not None:
                error = _check_locked_user(user, current_pin, request=request)
            elif password:
                error = _check_locked_user(user, password, password=True, request=request)
            else:
                error = HttpError(403, 'Enter your current PIN or verify your account password to reset it.')
        if error is None:
            user.set_pin(pin)
            user.save(update_fields=['pin_hash', 'pin_length', 'pin_lock_enabled',
                                     'pin_failed_attempts', 'pin_locked_until'])
    if error:
        raise error
    return user


def update_pin_protection(user_id, *, enabled=False, remove=False, current_pin=None, password=None, request=None):
    """Verify and mutate the same locked row; failed attempts must still commit."""
    with transaction.atomic():
        try:
            user = User.objects.select_for_update().get(pk=user_id, is_active=True)
        except User.DoesNotExist:
            raise HttpError(401, 'Session expired. Please sign in again.')
        error = None
        if enabled and not remove:
            if not user.has_pin:
                error = HttpError(400, 'You must set a PIN before enabling PIN lock.')
        elif user.has_pin:
            if current_pin is not None:
                error = _check_locked_user(user, current_pin, request=request)
            elif password:
                error = _check_locked_user(user, password, password=True, request=request)
            else:
                error = HttpError(403, 'Verify your current PIN or account password to continue.')
        if error is None:
            if remove:
                user.clear_pin()
                user.save(update_fields=['pin_hash', 'pin_length', 'pin_lock_enabled',
                                         'pin_failed_attempts', 'pin_locked_until'])
            else:
                user.pin_lock_enabled = enabled
                user.save(update_fields=['pin_lock_enabled'])
    if error:
        raise error
    return user
