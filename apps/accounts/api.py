import logging
import jwt
from ninja import Router

logger = logging.getLogger(__name__)
from ninja.errors import HttpError
from django.db import transaction
from django.db.models import Q

from .models import User, APIKey
from .auth import create_access_token, create_refresh_token, decode_token, jwt_auth, token_tenant_mismatch
from .schemas import (
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    AccessTokenResponse,
    UserSchema,
    UserCreateRequest,
    UserUpdateRequest,
    APIKeyCreateRequest,
    APIKeyCreatedResponse,
    APIKeyListItem,
    ErrorResponse,
    StaffSchema,
)

# ---------------------------------------------------------------------------
# TherapyPMS bcrypt helper
# ---------------------------------------------------------------------------

def _verify_tpms_password(plain: str, hashed: str | None) -> bool:
    """Verify a Laravel bcrypt hash ($2y$ or $2a$) against a plain-text password."""
    if not hashed:
        return False
    try:
        import bcrypt
        # uses $2y$; older PHP used $2a$. Python bcrypt expects $2b$.
        normalized = hashed.replace('$2y$', '$2b$', 1).replace('$2a$', '$2b$', 1).encode()
        result = bcrypt.checkpw(plain.encode(), normalized)
        return result
    except Exception as e:
        logger.warning('bcrypt verification error: %s | hash prefix: %s', e, hashed[:7] if hashed else None)
        return False


def _tpms_role_for_employee_type(employee_type: str | None) -> str:
    """Map a TPMS employee_type string to a DCM role."""
    if not employee_type:
        return User.Role.STAFF
    et = employee_type.lower()
    if 'bcba' in et or 'supervisor' in et or 'admin' in et:
        return User.Role.SUPERVISOR
    return User.Role.STAFF

router = Router()


def _issue_tokens(user: User, tenant_id: int) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user, tenant_id),
        refresh_token=create_refresh_token(user, tenant_id),
        user_id=user.id,
        email=user.email,
        role=user.role,
        full_name=user.full_name,
    )


@router.post('/login', response=TokenResponse, auth=None)
def login(request, data: LoginRequest):
    # Native (non-TPMS) users: a real password, bound to this request's tenant.
    # TPMS-provisioned users always have set_unusable_password() called on
    # them, so they can never match this branch — it's purely additive.
    native_user = User.objects.filter(email=data.email, organization=request.tenant).first()
    if native_user and native_user.has_usable_password() and native_user.check_password(data.password):
        if not native_user.is_active:
            raise HttpError(403, 'Account is inactive')
        return _issue_tokens(native_user, request.tenant.pk)

    # Otherwise authenticate against TPMS — no manual DCM user creation needed.
    # A DCM user record is auto-provisioned transparently on first login so the
    # JWT system has a user object to reference. The issued token is still
    # bound to *this* request's tenant, same as the native path above.
    return _tpms_auth(request, data.email, data.password)


def _tpms_effective_admin_id(admin) -> int:
    """
    Mirror TPMS login logic: is_up_admin=1 means this is the top-level practice
    owner — use their own id. is_up_admin=0 means sub-admin — use up_admin_id
    (the parent practice owner's id) so data scoping matches TPMS exactly.
    """
    if admin.is_up_admin == 1:
        return admin.id
    return admin.up_admin_id or admin.id


def _tpms_auth(request, email: str, password: str) -> TokenResponse:
    """Verify credentials against TPMS admins/employees and return a DCM token."""
    from apps.legacy.models import TpmsAdmin, TpmsEmployee
    from django.db.models import Q

    # Only match TPMS records belonging to the practice mapped to this tenant.
    # request.tenant is resolved purely from the Host header (see
    # shared/middleware.py). If no tenant was resolved, reject the login
    # attempt rather than continuing with a `None` tenant (which would raise
    # an AttributeError below).
    tenant = getattr(request, 'tenant', None)
    tenant_tpms_admin_id = tenant.tpms_admin_id if tenant else None

    # Collect candidate records from both tables, then pick the one whose
    # password verifies. A user can exist in admins (practice owner) AND
    # employees (provider) — we must try both.
    candidates: list[dict] = []

    try:
        admin = TpmsAdmin.objects.using('therapypms').get(
            Q(email=email) | Q(login_email=email)
        )
        if admin.password and (tenant_tpms_admin_id is None or _tpms_effective_admin_id(admin) == tenant_tpms_admin_id):
            candidates.append({
                'hashed_password': admin.password,
                'is_active': bool(admin.active),
                'first_name': admin.first_name or admin.name or '',
                'last_name': admin.last_name or '',
                'dcm_role': User.Role.ADMIN,
                'external_admin_id': _tpms_effective_admin_id(admin),
            })
    except TpmsAdmin.DoesNotExist:
        pass
    except TpmsAdmin.MultipleObjectsReturned:
        for admin in TpmsAdmin.objects.using('therapypms').filter(
            Q(email=email) | Q(login_email=email)
        ):
            if admin.password and (tenant_tpms_admin_id is None or _tpms_effective_admin_id(admin) == tenant_tpms_admin_id):
                candidates.append({
                    'hashed_password': admin.password,
                    'is_active': bool(admin.active),
                    'first_name': admin.first_name or admin.name or '',
                    'last_name': admin.last_name or '',
                    'dcm_role': User.Role.ADMIN,
                    'external_admin_id': _tpms_effective_admin_id(admin),
                })

    external_employee_id: int | None = None
    try:
        employee = TpmsEmployee.objects.using('therapypms').get(login_email=email)
        if employee.password and (tenant_tpms_admin_id is None or employee.admin_id == tenant_tpms_admin_id):
            external_employee_id = employee.id
            candidates.append({
                'hashed_password': employee.password,
                'is_active': bool(employee.is_staff_active or employee.is_active),
                'first_name': employee.first_name or '',
                'last_name': employee.last_name or '',
                'dcm_role': _tpms_role_for_employee_type(employee.employee_type),
                'external_admin_id': employee.admin_id,
            })
    except TpmsEmployee.DoesNotExist:
        pass

    # Try each candidate in order — first password match wins
    matched = next(
        (c for c in candidates if _verify_tpms_password(password, c['hashed_password'])),
        None,
    )

    if matched is None:
        raise HttpError(401, 'Invalid email or password')

    first_name  = matched['first_name']
    last_name   = matched['last_name']
    dcm_role    = matched['dcm_role']
    is_active   = matched['is_active']
    external_admin_id = matched['external_admin_id']

    if not is_active:
        raise HttpError(403, 'Account is inactive')

    # Auto-provision DCM user on first TPMS login; keep external_admin_id + external_employee_id current
    with transaction.atomic():
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'first_name': first_name,
                'last_name': last_name,
                'role': dcm_role,
                'is_active': True,
                'external_admin_id': external_admin_id,
                'external_employee_id': external_employee_id,
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=['password'])
        else:
            update_fields = []
            if user.external_admin_id != external_admin_id:
                user.external_admin_id = external_admin_id
                update_fields.append('external_admin_id')
            if user.external_employee_id != external_employee_id:
                user.external_employee_id = external_employee_id
                update_fields.append('external_employee_id')
            if user.role != dcm_role:
                user.role = dcm_role
                update_fields.append('role')
            if update_fields:
                user.save(update_fields=update_fields)

    return _issue_tokens(user, request.tenant.pk)



@router.post('/refresh', response=AccessTokenResponse, auth=None)
def refresh_token(request, data: RefreshRequest):
    try:
        payload = decode_token(data.refresh_token)
        if payload.get('type') != 'refresh':
            raise HttpError(401, 'Invalid token type')
        if token_tenant_mismatch(payload, request):
            raise HttpError(401, 'Invalid or expired token')
        user = User.objects.get(id=int(payload['sub']), is_active=True)
        # Reuse the tenant this refresh token was issued for — not
        # request.tenant again — so a refresh can never move a session to a
        # different tenant even if somehow presented elsewhere (defense in
        # depth; token_tenant_mismatch above already blocks that case).
        return AccessTokenResponse(access_token=create_access_token(user, payload['org_id']))
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, User.DoesNotExist, KeyError):
        raise HttpError(401, 'Invalid or expired token')


@router.get('/me', response=UserSchema, auth=jwt_auth)
def me(request):
    return request.user


@router.get('/me/debug', auth=jwt_auth)
def me_debug(request):
    """Temporary: shows raw TPMS lookup results for the logged-in user."""
    from apps.legacy.models import TpmsEmployee, TpmsAppointment
    from apps.clients.models import Client

    out: dict = {
        'dcm_email': request.user.email,
        'dcm_role': request.user.role,
        'dcm_external_admin_id': request.user.external_admin_id,
    }

    try:
        emp = TpmsEmployee.objects.using('therapypms').get(login_email=request.user.email)
        out['external_employee_id'] = emp.id
        out['tpms_employee_name'] = f'{emp.first_name} {emp.last_name}'
        out['tpms_employee_admin_id'] = emp.admin_id

        appt_client_ids = list(
            TpmsAppointment.objects.using('therapypms')
            .filter(provider_id=emp.id)
            .exclude(status__in=['deleted', 'void', 'voided'])
            .exclude(client_id__isnull=True)
            .values_list('client_id', flat=True)
            .distinct()
        )
        out['tpms_appointment_client_ids'] = appt_client_ids

        dcm_clients = list(
            Client.objects.filter(external_id__in=[str(c) for c in appt_client_ids])
            .values('id', 'first_name', 'last_name', 'external_id', 'external_admin_id')
        )
        out['dcm_matching_clients'] = dcm_clients
    except TpmsEmployee.DoesNotExist:
        out['tpms_employee_error'] = f'No TpmsEmployee found with login_email={request.user.email!r}'
    except TpmsEmployee.MultipleObjectsReturned:
        dupes = list(
            TpmsEmployee.objects.using('therapypms')
            .filter(login_email=request.user.email)
            .values('id', 'first_name', 'last_name', 'admin_id')
        )
        out['tpms_employee_error'] = f'Multiple TpmsEmployee records for that email: {dupes}'

    return out


# ---------------------------------------------------------------------------
# User management (admin only)
# ---------------------------------------------------------------------------

def _same_practice_q(user: User, prefix: str = '') -> Q:
    """
    Scopes a queryset to users in the same practice as `user` — either the
    same Organization (native users) or the same TPMS external_admin_id
    (externally-linked users). `prefix` lets this reach through a related
    field, e.g. _same_practice_q(user, 'created_by__') for APIKey.

    User/APIKey live in SHARED_APPS — one global table for every tenant on
    the platform, not schema-isolated — so without this filter, these
    admin-only endpoints would read/modify another tenant's users or keys
    given nothing more than a role check and a guessable id.
    """
    if user.organization_id is not None:
        return Q(**{f'{prefix}organization_id': user.organization_id})
    if user.external_admin_id is not None:
        return Q(**{f'{prefix}external_admin_id': user.external_admin_id})
    # Neither identifier set — scope to nothing rather than risk matching
    # every other user who also happens to have both fields null.
    return Q(**{f'{prefix}pk': None})


@router.get('/users', response=list[UserSchema], auth=jwt_auth)
def list_users(request):
    if not request.user.has_role('admin', 'supervisor'):
        raise HttpError(403, 'Insufficient permissions')
    return list(
        User.objects.filter(_same_practice_q(request.user), is_active=True)
        .order_by('last_name', 'first_name')
    )


@router.get('/admin/staffs', response=list[StaffSchema], auth=jwt_auth)
def list_admin_staffs(request, include_inactive: bool = False):
    """Return staff from the TPMS employees table for the logged-in admin's practice.

    By default returns only active staff (is_active=1), matching TPMS default view.
    Pass ?include_inactive=true to include inactive staff as well.
    """
    if not request.user.has_role('admin', 'supervisor'):
        raise HttpError(403, 'Admin or supervisor access required')
    if request.user.external_admin_id is None:
        return _list_native_staffs(request, include_inactive)
    from apps.legacy.models import TpmsEmployee
    qs = TpmsEmployee.objects.using('therapypms').filter(
        admin_id=request.user.external_admin_id,
    )
    if not include_inactive:
        qs = qs.filter(is_active=1)
    return list(qs.order_by('last_name', 'first_name'))


def _list_native_staffs(request, include_inactive: bool) -> list[StaffSchema]:
    """Native (non-TPMS) equivalent of list_admin_staffs — lists local Users
    bound to this admin's Organization instead of TPMS employees."""
    if request.user.organization_id is None:
        return []
    qs = User.objects.filter(organization_id=request.user.organization_id)
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return [
        StaffSchema(
            id=u.id,
            admin_id=None,
            first_name=u.first_name,
            last_name=u.last_name,
            full_name=u.full_name,
            login_email=u.email,
            office_email=None,
            employee_type=u.role,
            is_active=u.is_active,
            dcm_user_id=u.id,
        )
        for u in qs.order_by('last_name', 'first_name')
    ]


@router.post('/users', response={201: UserSchema, 400: ErrorResponse}, auth=jwt_auth)
def create_user(request, data: UserCreateRequest):
    if not request.user.has_role('admin'):
        raise HttpError(403, 'Admin access required')
    if User.objects.filter(email=data.email).exists():
        return 400, ErrorResponse(detail='A user with this email already exists')
    user = User.objects.create_user(
        email=data.email,
        first_name=data.first_name,
        last_name=data.last_name,
        role=data.role,
        password=data.password,
        organization=request.user.organization,
    )
    return 201, user


@router.patch('/users/{user_id}', response=UserSchema, auth=jwt_auth)
def update_user(request, user_id: int, data: UserUpdateRequest):
    if not request.user.has_role('admin'):
        raise HttpError(403, 'Admin access required')
    try:
        user = User.objects.get(_same_practice_q(request.user), id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(user, field, value)
    user.save()
    return user


# ---------------------------------------------------------------------------
# API key management (admin only)
# ---------------------------------------------------------------------------

@router.get('/api-keys', response=list[APIKeyListItem], auth=jwt_auth)
def list_api_keys(request):
    if not request.user.has_role('admin'):
        raise HttpError(403, 'Admin access required')
    return list(
        APIKey.objects.filter(_same_practice_q(request.user, 'created_by__'), is_active=True)
        .order_by('-created_at')
    )


@router.post('/api-keys', response={201: APIKeyCreatedResponse}, auth=jwt_auth)
def create_api_key(request, data: APIKeyCreateRequest):
    if not request.user.has_role('admin'):
        raise HttpError(403, 'Admin access required')
    key_instance, raw_key = APIKey.generate(
        name=data.name,
        created_by=request.user,
        expires_at=data.expires_at,
    )
    return 201, APIKeyCreatedResponse(
        id=key_instance.id,
        name=key_instance.name,
        key_prefix=key_instance.key_prefix,
        raw_key=raw_key,
        expires_at=key_instance.expires_at,
    )


@router.delete('/api-keys/{key_id}', response={204: None}, auth=jwt_auth)
def revoke_api_key(request, key_id: int):
    if not request.user.has_role('admin'):
        raise HttpError(403, 'Admin access required')
    try:
        key = APIKey.objects.get(_same_practice_q(request.user, 'created_by__'), id=key_id)
    except APIKey.DoesNotExist:
        raise HttpError(404, 'API key not found')
    key.is_active = False
    key.save(update_fields=['is_active'])
    return 204, None

@router.get('/admin/logs', auth=jwt_auth)
def get_logs(request, limit: int = 200):
    if not request.user.has_role('admin'):
        raise HttpError(403, 'Admin access required')
    from shared.log_buffer import get_recent_logs
    return {'logs': get_recent_logs(limit)}
