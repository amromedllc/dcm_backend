from datetime import datetime
from ninja import Schema
from pydantic import EmailStr, Field

from shared.schema_types import NonEmptyStr
from .models import User


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


class LoginResponse(Schema):
    """Either a full token set, or (when the account needs a code) an MFA
    challenge with no tokens."""
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = 'bearer'
    user_id: int | None = None
    email: str | None = None
    role: str | None = None
    full_name: str | None = None
    mfa_required: bool = False
    mfa_setup_required: bool = False
    mfa_token: str | None = None


class MfaTokenRequest(Schema):
    mfa_token: str


class MfaTokenCodeRequest(Schema):
    mfa_token: str
    code: str


class MfaCodeRequest(Schema):
    code: str


class MfaSetupResponse(Schema):
    secret: str
    otpauth_uri: str
    qr_code: str


class MfaStatusSchema(Schema):
    enabled: bool
    required: bool


class MfaRemoveRequest(Schema):
    password: str


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
    is_superuser: bool
    external_admin_id: int | None
    external_employee_id: int | None
    mfa_required: bool = False
    mfa_enabled: bool = False
    created_at: datetime


class CaregiverClientSchema(Schema):
    external_id: int
    full_name: str


class CurrentUserSchema(UserSchema):
    permissions: dict[str, bool]
    automatic_logout_minutes: int | None = None
    caregiver_client: CaregiverClientSchema | None = None


class AccountTimezoneOptionSchema(Schema):
    value: str
    label: str


class AccountProfileSchema(Schema):
    display_name: str
    email: str
    timezone: str | None = None
    timezone_options: list[AccountTimezoneOptionSchema] = []


class UserCreateRequest(Schema):
    email: EmailStr
    first_name: NonEmptyStr
    last_name: NonEmptyStr
    role: User.Role
    password: str = Field(min_length=8)


class UserUpdateRequest(Schema):
    first_name: NonEmptyStr | None = None
    last_name: NonEmptyStr | None = None
    role: User.Role | None = None
    is_active: bool | None = None
    mfa_required: bool | None = None


# Partner API-key schemas live in apps.tenants.schemas — keys are a
# superadmin-only concern (see apps.tenants.api).


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
    mfa_required: bool = False
    mfa_enabled: bool = False
