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


def create_access_token(user: User, tenant_id: int) -> str:
    payload: dict[str, Any] = {
        'sub': str(user.id),
        'email': user.email,
        'role': user.role,
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
        'iat': timezone.now().timestamp(),
        'exp': (timezone.now() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp(),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user: User, tenant_id: int) -> str:
    payload: dict[str, Any] = {
        'sub': str(user.id),
        'org_id': tenant_id,
        'type': 'refresh',
        'jti': secrets.token_hex(16),
        'iat': timezone.now().timestamp(),
        'exp': (timezone.now() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)).timestamp(),
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
            if payload.get('type') != 'access':
                return None
            if token_tenant_mismatch(payload, request):
                return None
            if is_token_blocked(payload):
                return None
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
    param_name = 'X-API-Key'

    def authenticate(self, request, key: str) -> APIKey | None:
        api_key = APIKey.verify(key)
        if api_key:
            request.api_key = api_key
            return api_key
        return None


jwt_auth = JWTAuth()
jwt_auth_any_role = AnyRoleJWTAuth()
caregiver_auth = CaregiverJWTAuth()
api_key_auth = APIKeyAuth()
