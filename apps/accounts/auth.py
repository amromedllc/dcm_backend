import math
import secrets
import jwt
from datetime import timedelta
from typing import Any
from django.conf import settings
from django.utils import timezone
from ninja.security import HttpBearer, APIKeyHeader

from .models import User, APIKey

# ---------------------------------------------------------------------------
# Token blocklist (Redis-backed)
# ---------------------------------------------------------------------------

def _redis():
    import redis
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _blocklist_key(jti: str) -> str:
    return f'dcm:token:blocked:{jti}'


def blocklist_token(payload: dict) -> None:
    """Add a token's jti to the blocklist until it naturally expires."""
    jti = payload.get('jti')
    exp = payload.get('exp')
    if not jti or not exp:
        return
    ttl = int(exp - timezone.now().timestamp())
    if ttl > 0:
        _redis().setex(_blocklist_key(jti), ttl, '1')


def consume_refresh_token(payload: dict) -> bool:
    """Atomically invalidate a refresh token through its PIN-unlock deadline.

    Its ordinary expiry may already have passed, so retaining the marker only
    until exp would let the same token unlock again inside the session window.
    """
    jti = payload.get('jti')
    if payload.get('type') != 'refresh' or not isinstance(jti, str) or not jti:
        raise jwt.InvalidTokenError('Invalid refresh token identity')
    start = session_start_from_payload(payload)
    ttl = max(1, math.ceil(start + settings.AUTH_SESSION_MAX_HOURS * 3600 - timezone.now().timestamp()))
    return bool(_redis().set(_blocklist_key(jti), '1', nx=True, ex=ttl))


def is_token_blocked(payload: dict) -> bool:
    jti = payload.get('jti')
    user_id = payload.get('sub')
    iat = payload.get('iat')
    r = _redis()
    # Check individual token blocklist
    if jti and r.exists(_blocklist_key(jti)):
        return True
    # Check logout-all revocation timestamp
    if user_id and iat:
        revoke_before = r.get(f'dcm:token:revoke_before:{user_id}')
        if revoke_before and float(iat) < float(revoke_before):
            return True
    return False


def session_start_from_payload(payload: dict) -> float:
    """Legacy tokens use their signed issuance time, never the unlock time."""
    start = payload.get('session_started_at', payload.get('iat'))
    if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start):
        raise jwt.InvalidTokenError('Invalid session start')
    now = timezone.now().timestamp()
    if start <= 0 or start > now or now >= start + settings.AUTH_SESSION_MAX_HOURS * 3600:
        raise jwt.ExpiredSignatureError('Maximum session lifetime reached')
    return float(start)


def create_access_token(
    user: User,
    tenant_id: int,
    *,
    session_started_at: float | None = None,
    session_id: str | None = None,
    idle_started_at: float | None = None,
) -> str:
    now = timezone.now()
    start = now.timestamp() if session_started_at is None else session_start_from_payload({'session_started_at': session_started_at})
    deadline = start + settings.AUTH_SESSION_MAX_HOURS * 3600
    payload: dict[str, Any] = {
        'sub': str(user.id),
        'email': user.email,
        'role': user.role,
        'is_superuser': user.is_superuser,
        # The tenant resolved (by hostname) at the moment this token was
        # issued — NOT user.organization_id. TPMS-linked users have no
        # single Organization FK (organization_id is always None for them),
        # but every login still happens against one specific tenant
        # hostname, so the token must bind to *that*, or it would validate
        # identically against every other tenant's Host header. See
        # token_tenant_mismatch below — there is no exemption from this.
        'org_id': tenant_id,
        'type': 'access',
        'jti': secrets.token_hex(16),
        'iat': now.timestamp(),
        'session_started_at': start,
        'sid': session_id or secrets.token_hex(16),
        'idle_started_at': now.timestamp() if idle_started_at is None else idle_started_at,
        'exp': min((now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp(), deadline),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(
    user: User,
    tenant_id: int,
    *,
    session_started_at: float | None = None,
    session_id: str | None = None,
    idle_started_at: float | None = None,
) -> str:
    now = timezone.now()
    start = now.timestamp() if session_started_at is None else session_start_from_payload({'session_started_at': session_started_at})
    deadline = start + settings.AUTH_SESSION_MAX_HOURS * 3600
    # Inactivity is enforced by server session state; refresh credentials stay
    # usable during an active session, bounded by the absolute sign-in limit.
    lifetime = timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

    payload: dict[str, Any] = {
        'sub': str(user.id),
        'org_id': tenant_id,
        'type': 'refresh',
        'jti': secrets.token_hex(16),
        'iat': now.timestamp(),
        'session_started_at': start,
        'sid': session_id or secrets.token_hex(16),
        'idle_started_at': now.timestamp() if idle_started_at is None else idle_started_at,
        'exp': min((now + lifetime).timestamp(), deadline),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def token_tenant_mismatch(payload: dict[str, Any], request) -> bool:
    """Return True if the token was issued for a different tenant than the current request."""
    token_org_id = payload.get('org_id')
    request_tenant = getattr(request, 'tenant', None)
    if token_org_id and request_tenant:
        return int(token_org_id) != request_tenant.pk
    return False


class _BaseJWTAuth(HttpBearer):
    """Decodes + validates a DCM access token, then optionally restricts
    which roles may authenticate through this instance at all.

    allowed_roles=None means any role. Enumerating roles POSITIVELY (rather
    than excluding a role) means a future role is denied-by-default rather
    than silently let through — see JWTAuth below, which is the API's
    default auth and must never accept a caregiver token.
    """
    allowed_roles: frozenset[str] | None = None

    def authenticate(self, request, token: str) -> User | None:
        try:
            payload = decode_token(token)
            session_start_from_payload(payload)
            if payload.get('type') != 'access':
                return None
            if token_tenant_mismatch(payload, request):
                return None
            if is_token_blocked(payload):
                return None
            from .session_security import enforce_session_activity
            enforce_session_activity(payload, getattr(request, 'tenant', None))
            user = User.objects.get(id=int(payload['sub']), is_active=True)
            if self.allowed_roles is not None and user.role not in self.allowed_roles:
                return None
            request.user = user
            request._jwt_payload = payload
            return user
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, User.DoesNotExist, KeyError):
            return None


class JWTAuth(_BaseJWTAuth):
    """The API's default auth. Excludes caregiver by construction — every
    endpoint using the default auth (i.e. almost the whole app) fails
    closed for portal accounts with no per-endpoint check required. See
    apps.caregiver_portal for the only caregiver-reachable surface."""
    allowed_roles = frozenset({User.Role.ADMIN, User.Role.SUPERVISOR, User.Role.STAFF, User.Role.REPORTING})


class AnyRoleJWTAuth(_BaseJWTAuth):
    """No role restriction — only for role-agnostic session endpoints
    (/auth/me, /auth/logout, /auth/logout-all) that every role needs."""


class CaregiverJWTAuth(_BaseJWTAuth):
    allowed_roles = frozenset({User.Role.CAREGIVER})

    def authenticate(self, request, token: str) -> User | None:
        user = super().authenticate(request, token)
        if user is None or user.external_client_id is None:
            return None
        return user


class APIKeyAuth(APIKeyHeader):
    """X-API-Key auth for partner integrations.

    A key is bound to one Organization and a dedicated service User. By the
    time this runs, TenantResolverMiddleware has already resolved
    request.tenant from the same key (see shared/middleware.py); this rejects
    the request if anything put a different tenant on it. Keys are read-only
    (GET/HEAD/OPTIONS) unless created with can_write.
    """
    param_name = 'X-API-Key'

    _SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})

    def authenticate(self, request, key: str) -> APIKey | None:
        api_key = APIKey.verify(key)
        if api_key is None:
            return None

        if api_key.organization_id is None or api_key.service_user_id is None:
            return None

        tenant = getattr(request, 'tenant', None)
        if tenant is None or api_key.organization_id != tenant.pk:
            return None

        if not api_key.can_write and request.method not in self._SAFE_METHODS:
            return None

        service_user = api_key.service_user
        if service_user is None or not service_user.is_active:
            return None

        request.api_key = api_key
        request.user = service_user
        request._jwt_payload = {'org_id': api_key.organization_id}
        return api_key


jwt_auth = JWTAuth()
jwt_auth_any_role = AnyRoleJWTAuth()
caregiver_auth = CaregiverJWTAuth()
api_key_auth = APIKeyAuth()

# Auth accepted by partner-reachable domain routers: an interactive user JWT,
# or an organization-scoped X-API-Key. jwt_auth is tried first, so keyed
# access is a fallback that never shadows a real user token.
partner_auth = [jwt_auth, api_key_auth]
