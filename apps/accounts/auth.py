import jwt
from datetime import timedelta
from typing import Any
from django.conf import settings
from django.utils import timezone
from ninja.security import HttpBearer, APIKeyHeader

from .models import User, APIKey


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
        'iat': timezone.now().timestamp(),
        'exp': (timezone.now() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp(),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user: User, tenant_id: int) -> str:
    payload: dict[str, Any] = {
        'sub': str(user.id),
        'org_id': tenant_id,
        'type': 'refresh',
        'iat': timezone.now().timestamp(),
        'exp': (timezone.now() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)).timestamp(),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def token_tenant_mismatch(payload: dict[str, Any], request) -> bool:
    """
    True if the tenant this token was issued for (its `org_id` claim — the
    tenant resolved at login time, for every user, native or TPMS-linked)
    doesn't match the tenant resolved for the current request's hostname.

    There is deliberately no exemption for any user class: a token minted
    while authenticated against one tenant's domain must never validate
    against a different tenant's domain, even if the underlying User row
    has no single Organization FK (true for TPMS-linked users).
    """
    tenant = getattr(request, 'tenant', None)
    return tenant is None or payload.get('org_id') != tenant.pk


class JWTAuth(HttpBearer):
    def authenticate(self, request, token: str) -> User | None:
        try:
            payload = decode_token(token)
            if payload.get('type') != 'access':
                return None
            if token_tenant_mismatch(payload, request):
                return None
            user = User.objects.get(id=int(payload['sub']), is_active=True)
            request.user = user
            return user
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, User.DoesNotExist, KeyError):
            return None


class APIKeyAuth(APIKeyHeader):
    param_name = 'X-API-Key'

    def authenticate(self, request, key: str) -> APIKey | None:
        api_key = APIKey.verify(key)
        if api_key:
            request.api_key = api_key
            return api_key
        return None


jwt_auth = JWTAuth()
api_key_auth = APIKeyAuth()
