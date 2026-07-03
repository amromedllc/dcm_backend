from datetime import datetime
from ninja import Schema
from pydantic import EmailStr


class LoginRequest(Schema):
    email: EmailStr
    password: str


class TokenResponse(Schema):
    access_token: str
    refresh_token: str
    token_type: str = 'bearer'
    user_id: int
    email: str
    role: str
    full_name: str


class RefreshRequest(Schema):
    refresh_token: str


class AccessTokenResponse(Schema):
    access_token: str
    token_type: str = 'bearer'


class UserSchema(Schema):
    id: int
    email: str
    first_name: str
    last_name: str
    full_name: str
    role: str
    is_active: bool
    tpms_admin_id: int | None
    created_at: datetime


class UserCreateRequest(Schema):
    email: EmailStr
    first_name: str
    last_name: str
    role: str
    password: str


class UserUpdateRequest(Schema):
    first_name: str | None = None
    last_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class APIKeyCreateRequest(Schema):
    name: str
    expires_at: datetime | None = None


class APIKeyCreatedResponse(Schema):
    id: int
    name: str
    key_prefix: str
    raw_key: str
    expires_at: datetime | None
    message: str = 'Store this key securely — it will not be shown again.'


class APIKeyListItem(Schema):
    id: int
    name: str
    key_prefix: str
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ErrorResponse(Schema):
    detail: str


class StaffSchema(Schema):
    id: int
    admin_id: int | None
    first_name: str | None
    last_name: str | None
    full_name: str | None
    login_email: str | None
    office_email: str | None
    employee_type: str | None
    is_active: bool
    dcm_user_id: int | None = None
