import logging
import jwt
from ninja import Router, Body
from django.utils import timezone

logger = logging.getLogger(__name__)
from ninja.errors import HttpError
from django.db import transaction
from django.db.models import Q

from .models import User, APIKey
from .auth import create_access_token, create_refresh_token, decode_token, jwt_auth, token_tenant_mismatch
from .permissions import get_user_permissions, require_permission, resolve_permission_organization
from .schemas import (
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    AccessTokenResponse,
    UserSchema,
    CurrentUserSchema,
    UserCreateRequest,
    UserUpdateRequest,
    APIKeyCreateRequest,
    APIKeyCreatedResponse,
    APIKeyListItem,
    ErrorResponse,
    StaffSchema,
)
from apps.integrations.tpms_auth_client import (
    TpmsAuthError,
    authenticate as tpms_authenticate,
    clear_tpms_access_token,
    resolve_practice_admin_id,
    store_tpms_access_token,
)


def _tpms_role_for_employee_type(employee_type: str | None, *, is_admin: bool = False) -> str:
    """Map a TPMS employee_type / admin flag to a DCM role."""
    if is_admin:
        return User.Role.ADMIN
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
    """
    Authenticate exclusively via TherapyPMS HTTP APIs (encrypt → login).

    No DCM local-password check and no direct TherapyPMS database password
    verification. A DCM User row is auto-provisioned so JWTs have a subject.
    """
    return _tpms_auth(request, data.email, data.password)


def _tpms_auth(request, email: str, password: str) -> TokenResponse:
    """Verify credentials via TherapyPMS iOS encrypt/login APIs and issue a DCM token."""
    tenant = getattr(request, 'tenant', None)
    # print(f'[DCM LOGIN] email={email!r} password_len={len(password)}')
    # print(
    #     f'[DCM LOGIN] tenant={getattr(tenant, "schema_name", None)!r} '
    #     f'tpms_admin_id={getattr(tenant, "tpms_admin_id", None)!r}'
    # )
    if tenant is None:
        # print('[DCM LOGIN] ✗ reject: no tenant resolved from Host')
        raise HttpError(401, 'Invalid email or password')

    tenant_tpms_admin_id = tenant.tpms_admin_id
    if tenant_tpms_admin_id is None:
        # Fail closed — without a practice mapping we cannot safely scope the session.
        # print('[DCM LOGIN] ✗ reject: tenant.tpms_admin_id is not set')
        raise HttpError(401, 'Invalid email or password')

    try:
        # print('[DCM LOGIN] calling TherapyPMS API (encrypt → login) …')
        profile = tpms_authenticate(email, password)
    except TpmsAuthError as exc:
        message = str(exc) or 'Invalid email or password'
        # print(f'[DCM LOGIN] ✗ TPMS auth error: {message!r} payload={getattr(exc, "payload", None)}')
        if 'unavailable' in message.lower() or 'invalid response' in message.lower():
            raise HttpError(502, message) from exc
        raise HttpError(401, 'Invalid email or password') from exc

    if not profile.is_active:
        # print('[DCM LOGIN] ✗ reject: TPMS profile is inactive')
        raise HttpError(403, 'Account is inactive')

    external_admin_id = profile.external_admin_id
    if external_admin_id is None:
        existing = User.objects.filter(email__iexact=profile.email or email).first()
        if existing and existing.external_admin_id is not None:
            external_admin_id = existing.external_admin_id
            # print(
            #     '[DCM LOGIN] practice id missing in TPMS login payload; '
            #     f'using stored user.external_admin_id={external_admin_id}'
            # )
        elif profile.access_token:
            try:
                external_admin_id = resolve_practice_admin_id(profile.access_token)
            except Exception as exc:
                # print(f'[DCM LOGIN] practice-id probe error: {exc!r}')
                external_admin_id = None
            if external_admin_id is not None:
                # print(
                #     '[DCM LOGIN] practice id missing in TPMS login payload; '
                #     f'resolved via TPMS API admin_id={external_admin_id}'
                # )
                pass

        if external_admin_id is None and not profile.is_admin:
            # Staff/provider tokens are already practice-scoped by TherapyPMS;
            # bind first-time staff to this hostname's mapped practice.
            external_admin_id = tenant_tpms_admin_id
            # print(
            #     '[DCM LOGIN] practice id still missing after probes; '
            #     f'binding provider to tenant.tpms_admin_id={external_admin_id}'
            # )

        if external_admin_id is None:
            # print(
            #     '[DCM LOGIN] ✗ reject: TPMS login OK but practice id missing; '
            #     f'raw keys={sorted(profile.raw.keys()) if isinstance(profile.raw, dict) else type(profile.raw)} '
            #     f'raw={profile.raw}'
            # )
            logger.warning(
                'TPMS login succeeded but practice id missing for email=%s keys=%s',
                email,
                sorted(profile.raw.keys()) if isinstance(profile.raw, dict) else type(profile.raw),
            )
            raise HttpError(401, 'Invalid email or password')

    # Tenant binding (C-01): only accept users belonging to this org's practice.
    if external_admin_id != tenant_tpms_admin_id:
        # print(
        #     f'[DCM LOGIN] ✗ reject: practice mismatch '
        #     f'tpms_admin_id={external_admin_id} != tenant.tpms_admin_id={tenant_tpms_admin_id}'
        # )
        raise HttpError(401, 'Invalid email or password')

    # print(f'[DCM LOGIN] ✓ practice match admin_id={external_admin_id}; issuing DCM JWT')

    dcm_role = _tpms_role_for_employee_type(
        profile.employee_type,
        is_admin=profile.is_admin,
    )
    first_name = profile.first_name or ''
    last_name = profile.last_name or ''
    external_employee_id = profile.external_employee_id
    provision_email = profile.email or email

    # Auto-provision DCM user on first TPMS login; keep external ids + role current.
    # Bind organization to the login tenant so facility-scoped RolePermission
    # rows resolve correctly (privileges are per Organization).
    login_org = request.tenant
    with transaction.atomic():
        user, created = User.objects.get_or_create(
            email=provision_email,
            defaults={
                'first_name': first_name,
                'last_name': last_name,
                'role': dcm_role,
                'is_active': True,
                'external_admin_id': external_admin_id,
                'external_employee_id': external_employee_id,
                'organization': login_org,
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=['password'])
        else:
            update_fields = []
            if first_name and user.first_name != first_name:
                user.first_name = first_name
                update_fields.append('first_name')
            if last_name and user.last_name != last_name:
                user.last_name = last_name
                update_fields.append('last_name')
            if user.external_admin_id != external_admin_id:
                user.external_admin_id = external_admin_id
                update_fields.append('external_admin_id')
            if user.external_employee_id != external_employee_id:
                user.external_employee_id = external_employee_id
                update_fields.append('external_employee_id')
            if user.role != dcm_role:
                user.role = dcm_role
                update_fields.append('role')
            if login_org is not None and user.organization_id != login_org.pk:
                user.organization = login_org
                update_fields.append('organization')
            if not user.is_active:
                user.is_active = True
                update_fields.append('is_active')
            if update_fields:
                user.save(update_fields=update_fields)

    if profile.access_token:
        store_tpms_access_token(user.id, profile.access_token)
    else:
        # print('[DCM LOGIN] ⚠ TPMS login OK but access_token missing from payload')
        pass

    return _issue_tokens(user, tenant.pk)



@router.post('/logout', auth=jwt_auth, response={204: None})
def logout(request):
    """Revoke the current access token immediately. Token is blocklisted in Redis until expiry."""
    from .auth import blocklist_token
    payload = getattr(request, '_jwt_payload', {})
    blocklist_token(payload)
    clear_tpms_access_token(request.user.id)
    return 204, None


@router.post('/logout-all', auth=jwt_auth, response={204: None})
def logout_all(request):
    """
    Revoke all active tokens for this user by rotating their token secret seed.
    Achieved by storing a per-user revocation timestamp in Redis — any token
    issued before this timestamp is rejected.
    """
    import redis as redis_lib
    from django.conf import settings
    r = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    r.set(f'dcm:token:revoke_before:{request.user.id}', timezone.now().timestamp(), ex=60 * 60 * 24 * 30)
    clear_tpms_access_token(request.user.id)
    return 204, None



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


@router.get('/me', response=CurrentUserSchema, auth=jwt_auth)
def me(request):
    user = request.user
    org = resolve_permission_organization(request)
    user.permissions = get_user_permissions(user, org)
    return user


@router.get('/me/debug', auth=jwt_auth)
def me_debug(request):
    """Debug endpoint — admin only."""
    if not request.user.has_role('admin'):
        raise HttpError(403, 'Admin access required')
    if getattr(request, 'tenant', None) and not request.tenant.is_active:
        raise HttpError(403, 'Forbidden')
    from apps.clients.models import Client

    out: dict = {
        'dcm_email': request.user.email,
        'dcm_role': request.user.role,
        'dcm_external_admin_id': request.user.external_admin_id,
        'external_employee_id': request.user.external_employee_id,
    }

    if request.user.external_admin_id is not None:
        out['dcm_practice_clients'] = list(
            Client.objects.filter(external_admin_id=request.user.external_admin_id)
            .values('id', 'first_name', 'last_name', 'external_id', 'external_admin_id')[:50]
        )
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
    """Return staff for the logged-in admin's practice.

    TPMS-linked practices use local DCM User rows (synced at login) scoped by
    external_admin_id. Native practices use Organization membership.
    """
    if not request.user.has_role('admin', 'supervisor'):
        raise HttpError(403, 'Admin or supervisor access required')
    if request.user.external_admin_id is None:
        return _list_native_staffs(request, include_inactive)

    qs = User.objects.filter(external_admin_id=request.user.external_admin_id)
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return [
        StaffSchema(
            id=u.external_employee_id or u.id,
            admin_id=u.external_admin_id,
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
    require_permission(request, 'admin_users_edit')
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
    require_permission(request, 'admin_users_edit')
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


# ---------------------------------------------------------------------------
# Role permissions (admin only)
# ---------------------------------------------------------------------------

@router.get('/admin/role-permissions', auth=jwt_auth)
def get_role_permissions(request):
    """Return the full permission matrix as {role: {perm_key: bool}}.

    Rows are loaded for the login facility only (JWT / tenant).
    """
    require_permission(request, 'admin_privileges')
    from .models import RolePermission
    from .permissions import PERMISSION_DEFAULTS, _apply_role_guarantees

    org = resolve_permission_organization(request)
    result: dict = {
        role: dict(defaults)
        for role, defaults in PERMISSION_DEFAULTS.items()
    }
    rows = RolePermission.objects.filter(organization=org)
    for row in rows:
        merged = {**result.get(row.role, {}), **(row.permissions or {})}
        result[row.role] = _apply_role_guarantees(row.role, merged)
    for role in list(result.keys()):
        result[role] = _apply_role_guarantees(role, result[role])
    return result


@router.put('/admin/role-permissions', auth=jwt_auth)
def save_role_permissions(request, body: dict = Body(...)):
    """Save the permission matrix for the login facility.

    Body: {role: {perm_key: bool}}. Persists one RolePermission row per role
    under the Organization resolved from the logged-in user's JWT/tenant.
    """
    require_permission(request, 'admin_privileges')
    from .models import RolePermission
    from .permissions import PERMISSION_DEFAULTS, _apply_role_guarantees

    org = resolve_permission_organization(request)

    valid_roles = {c[0] for c in User.Role.choices}
    for role, perms in body.items():
        if role not in valid_roles:
            raise HttpError(400, f'Invalid role: {role}')
        if not isinstance(perms, dict):
            raise HttpError(400, f'Permissions must be an object for role {role}')

    saved: dict = {}
    for role, perms in body.items():
        # Supervisors must retain org-management tools to grant staff access.
        if role == User.Role.SUPERVISOR:
            perms = {
                **perms,
                'admin_users_view': True,
                'admin_users_edit': True,
                'admin_privileges': True,
            }
        # Any settings subsection implies Settings page access in the sidebar.
        if any(
            key.startswith('settings_') and key.endswith('_view') and key != 'settings_view' and bool(value)
            for key, value in perms.items()
        ):
            perms = {**perms, 'settings_view': True}
        RolePermission.objects.update_or_create(
            organization=org,
            role=role,
            defaults={'permissions': perms},
        )
        saved[role] = _apply_role_guarantees(role, {**PERMISSION_DEFAULTS.get(role, {}), **perms})

    return {
        'ok': True,
        'organization_id': org.pk,
        'organization_name': org.name,
        'permissions': saved,
    }
