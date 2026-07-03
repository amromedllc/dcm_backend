import jwt
from datetime import timedelta
from typing import Any
from django.conf import settings
from django.utils import timezone
from ninja.security import HttpBearer, APIKeyHeader

from .models import User, APIKey


def create_access_token(user: User) -> str:
    payload: dict[str, Any] = {
        'sub': str(user.id),
        'email': user.email,
        'role': user.role,
        'type': 'access',
        'iat': timezone.now().timestamp(),
        'exp': (timezone.now() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp(),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user: User) -> str:
    payload: dict[str, Any] = {
        'sub': str(user.id),
        'type': 'refresh',
        'iat': timezone.now().timestamp(),
        'exp': (timezone.now() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)).timestamp(),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


class JWTAuth(HttpBearer):
    def authenticate(self, request, token: str) -> User | None:
        try:
            payload = decode_token(token)
            if payload.get('type') != 'access':
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
