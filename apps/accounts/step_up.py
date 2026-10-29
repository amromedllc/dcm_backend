"""Single-use, session/action/body-bound approvals for sensitive account writes."""
import hashlib
import json
import re
import secrets

from ninja.errors import HttpError
from django.utils.crypto import salted_hmac
from redis.exceptions import RedisError

from . import mfa
from .auth import _redis
from .models import User

TTL = 300
_RESERVE = "local n=tonumber(redis.call('GET',KEYS[1]) or '0'); if n>=5 then return 0 end; redis.call('INCR',KEYS[1]); if n==0 then redis.call('EXPIRE',KEYS[1],300) end; return 1"
_CONSUME = "if redis.call('GET',KEYS[1]) == ARGV[1] then redis.call('DEL',KEYS[1]); return 1 end; return 0"
_ACTIONS = [(r'/auth/users', {'POST'}), (r'/auth/users/\d+', {'PATCH'}),
            (r'/auth/users/\d+/(password-link|mfa/remove)', {'POST'}),
            (r'/auth/admin/role-permissions', {'PUT'})]


def _action(path, method, digest):
    if not any(re.fullmatch(pattern, path) and method in methods for pattern, methods in _ACTIONS):
        raise HttpError(400, 'Unsupported verification action.')
    if not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise HttpError(400, 'Invalid action digest.')


def _actor(request):
    try:
        user = User.objects.select_related('mfa').get(pk=request.user.pk, is_active=True)
    except User.DoesNotExist:
        raise HttpError(401, 'Sign in again before changing security settings.')
    payload = getattr(request, '_jwt_payload', {})
    sid = payload.get('sid')
    org = getattr(getattr(request, 'tenant', None), 'pk', None)
    if not isinstance(sid, str) or not sid or not org or payload.get('org_id') != org:
        raise HttpError(401, 'Sign in again before changing security settings.')
    return user, sid, org


def _binding(user, sid, org, path, method, digest):
    factor = getattr(user, 'mfa', None)
    fingerprint = hashlib.sha256(json.dumps([user.password, user.mfa_required,
        getattr(factor, 'is_active', False), getattr(factor, 'method', None),
        getattr(factor, 'secret_encrypted', None), getattr(factor, 'pk', None),
        str(getattr(factor, 'confirmed_at', None))], sort_keys=True).encode()).hexdigest()
    binding = json.dumps([user.pk, org, sid, path, method, digest, fingerprint], separators=(',', ':'))
    return salted_hmac('accounts.step-up.binding', binding, algorithm='sha256').hexdigest()


def requirements(request):
    user, _, _ = _actor(request)
    return {'password_required': user.has_usable_password(), 'mfa_required': user.mfa_enabled,
            'mfa_method': user.mfa.method if user.mfa_enabled else None}


def verify(request, data):
    _action(data.path, data.method, data.digest)
    user, sid, org = _actor(request)
    if not user.has_usable_password() and not user.mfa_enabled:
        raise HttpError(403, 'Set up a password or MFA before changing security settings.')
    if user.mfa_required and not user.mfa_enabled:
        raise HttpError(403, 'Complete required MFA setup before changing security settings.')
    if not user.has_usable_password() and user.mfa_enabled:
        confirmed = getattr(user.mfa, 'confirmed_at', None)
        anchor = getattr(request, '_jwt_payload', {}).get('session_started_at')
        if confirmed is None or not isinstance(anchor, (int, float)) or confirmed.timestamp() > anchor:
            raise HttpError(403, 'Sign in again with MFA before changing security settings.')
    attempts = f'dcm:step-up:attempts:{org}:{user.pk}'
    try:
        redis = _redis()
        if not redis.eval(_RESERVE, 1, attempts):
            raise HttpError(429, 'Too many verification attempts. Try again in five minutes.')
        if user.has_usable_password() and not user.check_password(data.password or ''):
            raise HttpError(403, 'Incorrect account password.')
        if user.mfa_enabled:
            mfa.check_code(user, data.code or '')
        token = secrets.token_hex(32)
        redis.setex(f'dcm:step-up:grant:{token}', TTL,
                    _binding(user, sid, org, data.path, data.method, data.digest))
        # Successful checks do not use up the failed-attempt budget.
        redis.eval("if tonumber(redis.call('GET',KEYS[1]) or '0')>0 then return redis.call('DECR',KEYS[1]) end; return 0", 1, attempts)
        return {'verification_token': token, 'expires_in': TTL}
    except RedisError:
        raise HttpError(503, 'Secure verification is unavailable. Please retry.')


def require_step_up(request):
    user, sid, org = _actor(request)
    path = request.path.removeprefix('/api/v1')
    digest = hashlib.sha256(request.body).hexdigest()
    _action(path, request.method, digest)
    token = request.headers.get('X-Step-Up-Token', '')
    if not re.fullmatch(r'[a-f0-9]{64}', token):
        raise HttpError(428, 'Fresh verification is required for this action.')
    try:
        consumed = _redis().eval(_CONSUME, 1, f'dcm:step-up:grant:{token}',
            _binding(user, sid, org, path, request.method, digest))
    except RedisError:
        raise HttpError(503, 'Secure verification is unavailable. Please retry.')
    if not consumed:
        raise HttpError(428, 'Verification expired or does not match this action. Please verify again.')
