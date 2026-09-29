import hashlib
import logging
from datetime import datetime
import jwt
from ninja import Router, Body
from django.conf import settings
from django.contrib.auth import authenticate
from django.utils import timezone

logger = logging.getLogger(__name__)
from ninja.errors import HttpError
from django.db import transaction
from django.db.models import Q

from . import mfa
from .models import PasswordSetLink, User, UserMFA
from .auth import create_access_token, create_refresh_token, decode_token, jwt_auth, jwt_auth_any_role, token_tenant_mismatch
from .permissions import get_user_permissions, require_permission, resolve_permission_organization
from .schemas import (
    LoginRequest,
    LoginResponse,
    MfaAccountSetupStartRequest,
    MfaCodeRequest,
    MfaRemoveRequest,
    MfaSetupResponse,
    MfaSetupStartRequest,
    MfaStatusSchema,
    MfaTokenCodeRequest,
    MfaTokenRequest,
    TokenResponse,
    RefreshRequest,
    AccessTokenResponse,
    AccountProfileSchema,
    UserSchema,
    CurrentUserSchema,
    UserCreateRequest,
    UserUpdateRequest,
    ErrorResponse,
    PasswordSetLinkGenerateRequest,
    PasswordSetLinkGenerateResponse,
    PasswordSetLinkInfoResponse,
    PasswordSetLinkSubmitRequest,
    StaffSchema,
)
from apps.clients.models import Client
from apps.integrations.tpms_auth_client import (
    TpmsAuthError,
    authenticate_raw as tpms_authenticate_raw,
    account_type_of,
    normalize_client_portal_payload,
    clear_tpms_access_token,
    get_ios_personal_info,
    get_ios_time_zone,
    get_tpms_access_token,
    store_tpms_access_token,
)


_CLIENT_ACCOUNT_TYPES = {'client', 'patient'}

router = Router()


def _issue_tokens(user: User, tenant_id: int) -> TokenResponse:
    # This app issues JWTs directly rather than calling django.contrib.auth's
    # login(), so the user_logged_in signal that normally stamps last_login
    # never fires — do it here instead, since every login/token-issuing path
    # (password login, MFA verify, password-set-link) already funnels
    # through this one function. Downstream: the provider admin screen's
    # "Active" status is keyed off last_login being non-null.
    User.objects.filter(pk=user.pk).update(last_login=timezone.now())
    return TokenResponse(
        access_token=create_access_token(user, tenant_id),
        refresh_token=create_refresh_token(user, tenant_id),
        user_id=user.id,
        email=user.email,
        role=user.role,
        full_name=user.full_name,
    )


@router.post('/login', response=LoginResponse, auth=None)
def login(request, data: LoginRequest):
    """
    Staff/provider accounts authenticate locally against their DCM password
    — no TherapyPMS round trip. That password only exists once an admin has
    shared a password-set link and the provider has used it (see
    generate_password_set_link / submit_password_set_link below); a synced
    provider who hasn't done that yet cannot log in here, by design — sync
    (apps.integrations.tpms_pull.pull_providers) is now the only place a
    staff-side DCM User gets provisioned, not this endpoint.

    Caregiver/client-portal accounts are unaffected by that cutover and
    still authenticate live through TherapyPMS's /ios/login — see
    _tpms_caregiver_auth. A caregiver never has a usable DCM password, so
    the local_user lookup below deliberately excludes that role and falls
    through to the TPMS path unchanged.
    """
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise HttpError(401, 'Invalid email or password')

    local_user = User.objects.filter(email__iexact=data.email).exclude(role=User.Role.CAREGIVER).first()
    if local_user is not None:
        if local_user.is_superuser:
            superuser_tokens = _superadmin_local_auth(request, tenant, data.email, data.password)
            if superuser_tokens is None:
                raise HttpError(401, 'Invalid email or password')
            return _mfa_gate(tenant, superuser_tokens)
        return _mfa_gate(tenant, _staff_local_auth(tenant, local_user, data.password))

    try:
        payload = tpms_authenticate_raw(data.email, data.password)
    except TpmsAuthError as exc:
        message = str(exc) or 'Invalid email or password'
        if 'unavailable' in message.lower() or 'invalid response' in message.lower():
            raise HttpError(502, message) from exc
        raise HttpError(401, 'Invalid email or password') from exc

    if account_type_of(payload) in _CLIENT_ACCOUNT_TYPES:
        return _mfa_gate(tenant, _tpms_caregiver_auth(request, tenant, data.email, payload))
    raise HttpError(401, 'Invalid email or password')


def _mfa_gate(tenant, tokens: TokenResponse):
    """Hold back the tokens if this account must (or chose to) use MFA — the
    client gets a short-lived challenge token instead and finishes signing in
    through /mfa/verify or the /mfa/setup/* endpoints."""
    user = User.objects.select_related('mfa').get(id=tokens.user_id)
    if not mfa.needs_mfa(user):
        return tokens
    setup_required = not user.mfa_enabled
    method = None
    if not setup_required:
        method = user.mfa.method
        if method == UserMFA.Method.EMAIL:
            try:
                mfa.send_email_otp(user)
            except HttpError as exc:
                # A code was already sent within the resend cooldown (e.g. signing
                # back in moments after finishing setup) — that earlier code, or one
                # from a still-open tab, may still be usable, so let the challenge
                # through rather than blocking sign-in on a rate limit.
                if exc.status_code != 429:
                    raise
    return LoginResponse(
        mfa_required=True,
        mfa_setup_required=setup_required,
        mfa_method=method,
        mfa_token=mfa.create_mfa_token(user, tenant.pk),
    )


@router.post('/mfa/verify', response=TokenResponse, auth=None)
def mfa_verify(request, data: MfaTokenCodeRequest):
    tenant = getattr(request, 'tenant', None)
    user, org_id = mfa.user_from_mfa_token(data.mfa_token, getattr(tenant, 'pk', None))
    mfa.check_code(user, data.code)
    return _issue_tokens(user, org_id)


@router.post('/mfa/resend', response={204: None}, auth=None)
def mfa_login_resend(request, data: MfaTokenRequest):
    tenant = getattr(request, 'tenant', None)
    user, _org_id = mfa.user_from_mfa_token(data.mfa_token, getattr(tenant, 'pk', None))
    mfa.send_email_otp(user)
    return 204, None


@router.post('/mfa/setup/start', response=MfaSetupResponse, auth=None)
def mfa_login_setup_start(request, data: MfaSetupStartRequest):
    tenant = getattr(request, 'tenant', None)
    user, _org_id = mfa.user_from_mfa_token(data.mfa_token, getattr(tenant, 'pk', None))
    return mfa.start_setup(user, method=data.method)


@router.post('/mfa/setup/confirm', response=TokenResponse, auth=None)
def mfa_login_setup_confirm(request, data: MfaTokenCodeRequest):
    tenant = getattr(request, 'tenant', None)
    user, org_id = mfa.user_from_mfa_token(data.mfa_token, getattr(tenant, 'pk', None))
    mfa.check_code(user, data.code, activate=True)
    return _issue_tokens(user, org_id)


def _mfa_status(user: User) -> MfaStatusSchema:
    fresh = User.objects.select_related('mfa').get(id=user.id)
    method = fresh.mfa.method if hasattr(fresh, 'mfa') else None
    return MfaStatusSchema(enabled=fresh.mfa_enabled, required=fresh.mfa_required, method=method)


@router.get('/features', auth=jwt_auth_any_role)
def get_features(request):
    """Which optional features are switched on for this deployment (used to show or hide them in the web app)."""
    from shared import ai_client
    return {'ai_enabled': ai_client.is_enabled()}


@router.get('/account/mfa', response=MfaStatusSchema, auth=jwt_auth_any_role)
def get_my_mfa(request):
    return _mfa_status(request.user)


@router.post('/account/mfa/setup/start', response=MfaSetupResponse, auth=jwt_auth_any_role)
def start_my_mfa_setup(request, data: MfaAccountSetupStartRequest | None = None):
    return mfa.start_setup(request.user, method=data.method if data else UserMFA.Method.TOTP)


@router.post('/account/mfa/setup/confirm', response=MfaStatusSchema, auth=jwt_auth_any_role)
def confirm_my_mfa_setup(request, data: MfaCodeRequest):
    mfa.check_code(request.user, data.code, activate=True)
    return _mfa_status(request.user)


@router.post('/account/mfa/resend', response={204: None}, auth=jwt_auth_any_role)
def resend_my_mfa(request):
    mfa.send_email_otp(request.user)
    return 204, None


@router.post('/account/mfa/disable', response={204: None}, auth=jwt_auth_any_role)
def disable_my_mfa(request, data: MfaCodeRequest):
    if request.user.mfa_required:
        raise HttpError(400, 'Your organization requires MFA, so it cannot be turned off.')
    mfa.check_code(request.user, data.code)
    UserMFA.objects.filter(user=request.user).delete()
    return 204, None


def _verify_actor_password(user: User, password: str) -> bool:
    return user.has_usable_password() and user.check_password(password)


@router.post('/users/{user_id}/mfa/remove', response={204: None}, auth=jwt_auth)
def remove_user_mfa(request, user_id: int, data: MfaRemoveRequest):
    require_permission(request, 'admin_users_edit')
    try:
        user = User.objects.get(_same_practice_q(request.user), id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')
    if not _verify_actor_password(request.user, data.password):
        raise HttpError(403, 'Incorrect password')
    UserMFA.objects.filter(user=user).delete()
    return 204, None


def _superadmin_local_auth(request, tenant, email: str, password: str) -> TokenResponse | None:
    """Allow platform superusers into the web app with their Django password.

    Regular users must still authenticate through TherapyPMS; this fallback is
    only for platform-owned superadmin tools that used to require /admin.
    """
    user = authenticate(request, username=email, password=password)
    if user is None or not user.is_active or not user.is_superuser:
        return None
    if tenant is not None and user.organization_id is None:
        user.organization = tenant
        user.save(update_fields=['organization'])
    tenant_id = tenant.pk if tenant is not None else user.organization_id
    if tenant_id is None:
        logger.error('Superadmin local login has no tenant context for email=%s', email)
        raise HttpError(401, 'Invalid email or password')
    return _issue_tokens(user, tenant_id)


def _staff_local_auth(tenant, user: User, password: str) -> TokenResponse:
    """Local-password staff/provider login — no TherapyPMS round trip.

    The user must already have a DCM password (set via an admin-issued
    password-set link, or created directly for a native/non-TPMS org — see
    create_user) and belong to this tenant. Credentials are checked before
    is_active, same order the old TPMS-proxied path used, so a wrong
    password never reveals whether the account is merely deactivated.
    """
    if not user.has_usable_password() or not user.check_password(password):
        raise HttpError(401, 'Invalid email or password')
    if not user.is_active:
        raise HttpError(403, 'Account is inactive')

    tenant_admin_ids = set(tenant.tpms_admin_ids.values_list('admin_id', flat=True))
    if tenant_admin_ids:
        if user.external_admin_id not in tenant_admin_ids:
            raise HttpError(401, 'Invalid email or password')
    elif user.organization_id != tenant.pk:
        # Native (non-TPMS) org — bind by Organization membership instead.
        raise HttpError(401, 'Invalid email or password')

    return _issue_tokens(user, tenant.pk)


def _tpms_caregiver_auth(request, tenant, email: str, payload: dict) -> TokenResponse:
    """Provision/refresh a caregiver DCM session bound to exactly one client.

    No sibling-list/sibling-login step (deliberate — see
    normalize_client_portal_payload's docstring): the base login payload's
    user.id already identifies the one linked patient.
    """
    tenant_admin_ids = set(tenant.tpms_admin_ids.values_list('admin_id', flat=True))
    if not tenant_admin_ids:
        raise HttpError(401, 'Invalid email or password')

    try:
        profile = normalize_client_portal_payload(email, payload)
    except TpmsAuthError:
        raise HttpError(401, 'Invalid email or password')

    # Client.objects is tenant-scoped by TenantManager under the request's
    # tenant_context (set by TenantResolverMiddleware before this view runs),
    # so this lookup can only ever match a client in the login hostname's org.
    clients = list(Client.objects.filter(external_id=str(profile.external_client_id)).order_by('id'))
    if not clients:
        raise HttpError(403, 'Your portal account is not set up in DCM yet. Please contact your provider.')
    if len({c.external_admin_id for c in clients}) > 1:
        logger.error(
            'Ambiguous portal client external_id=%s resolved to multiple practices in org=%s',
            profile.external_client_id, tenant.pk,
        )
        raise HttpError(403, 'Invalid email or password')
    client = clients[0]

    # Practice binding — the caregiver analogue of the staff tenant check
    # above, using the Client row's own practice rather than a profile
    # field (a client-portal payload carries no practice id of its own).
    # Never guess a practice for a caregiver the way the staff path's
    # single-practice fallback does — always require an explicit match.
    if client.external_admin_id is None or client.external_admin_id not in tenant_admin_ids:
        raise HttpError(401, 'Invalid email or password')

    provision_email = profile.email or email

    existing_row = User.objects.filter(email__iexact=provision_email).first()
    if existing_row and existing_row.role != User.Role.CAREGIVER:
        logger.error('Portal login email collides with a non-caregiver user id=%s', existing_row.id)
        raise HttpError(409, 'This email is already registered as a staff account.')

    with transaction.atomic():
        user, created = User.objects.get_or_create(
            email=provision_email,
            defaults={
                'first_name': profile.first_name,
                'last_name': profile.last_name,
                'role': User.Role.CAREGIVER,
                'is_active': True,
                'external_client_id': profile.external_client_id,
                'external_admin_id': client.external_admin_id,
                'external_employee_id': None,
                'organization': tenant,
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=['password'])
        else:
            update_fields = []
            if profile.first_name and user.first_name != profile.first_name:
                user.first_name = profile.first_name
                update_fields.append('first_name')
            if profile.last_name and user.last_name != profile.last_name:
                user.last_name = profile.last_name
                update_fields.append('last_name')
            # Re-link rather than silently keep a stale pointer if the
            # portal account now maps to a different patient/practice.
            if user.external_client_id != profile.external_client_id:
                user.external_client_id = profile.external_client_id
                update_fields.append('external_client_id')
            if user.external_admin_id != client.external_admin_id:
                user.external_admin_id = client.external_admin_id
                update_fields.append('external_admin_id')
            if user.organization_id != tenant.pk:
                user.organization = tenant
                update_fields.append('organization')
            if not user.is_active:
                user.is_active = True
                update_fields.append('is_active')
            if update_fields:
                user.save(update_fields=update_fields)

    if profile.access_token:
        store_tpms_access_token(user.id, profile.access_token)

    return _issue_tokens(user, tenant.pk)


@router.post('/logout', auth=jwt_auth_any_role, response={204: None})
def logout(request):
    """Revoke the current access token immediately. Token is blocklisted in Redis until expiry."""
    from .auth import blocklist_token
    payload = getattr(request, '_jwt_payload', {})
    blocklist_token(payload)
    clear_tpms_access_token(request.user.id)
    return 204, None


@router.post('/logout-all', auth=jwt_auth_any_role, response={204: None})
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


@router.get('/me', response=CurrentUserSchema, auth=jwt_auth_any_role)
def me(request):
    user = request.user
    tenant = getattr(request, 'tenant', None)
    user.automatic_logout_minutes = (
        tenant.automatic_logout_minutes
        if tenant is not None and tenant.automatic_logout_enabled
        else None
    )
    if user.is_caregiver:
        # Caregiver access is identity-scoped, not permission-key-scoped —
        # no RolePermission lookup, just the linked client's display info.
        user.permissions = {}
        client = Client.objects.filter(external_id=str(user.external_client_id)).first()
        user.caregiver_client = (
            {'external_id': user.external_client_id, 'full_name': client.full_name} if client else None
        )
        return user
    org = resolve_permission_organization(request)
    user.permissions = get_user_permissions(user, org)
    user.caregiver_client = None
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


def _tpms_unwrap(payload: dict) -> dict:
    for key in ('data', 'user', 'result', 'personal_info', 'personalInfo', 'profile'):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return {**payload, **nested}
    return payload


def _tpms_value(data: dict, *keys: str) -> str:
    for key in keys:
        if key in data and data[key] not in (None, ''):
            if isinstance(data[key], (dict, list)):
                continue
            return str(data[key]).strip()
        lower = key.lower()
        for existing, value in data.items():
            if existing.lower() == lower and value not in (None, ''):
                if isinstance(value, (dict, list)):
                    continue
                return str(value).strip()
    return ''


def _timezone_options(payload: dict) -> list[dict[str, str]]:
    rows = []
    for key in ('timezones', 'timezone_list', 'timezoneList', 'data', 'result'):
        value = payload.get(key)
        if isinstance(value, list):
            rows = value
            break
        if isinstance(value, dict):
            return [
                {'value': str(option_value), 'label': str(option_label)}
                for option_value, option_label in value.items()
                if option_value
            ]

    time_zone_map = payload.get('time_zone')
    if isinstance(time_zone_map, dict):
        return [
            {'value': str(option_value), 'label': str(option_label)}
            for option_value, option_label in time_zone_map.items()
            if option_value
        ]

    options: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, str) and row:
            options.append({'value': row, 'label': row})
        elif isinstance(row, dict):
            value = _tpms_value(row, 'value', 'timezone', 'time_zone', 'id', 'name')
            label = _tpms_value(row, 'label', 'name', 'timezone', 'time_zone', 'display_name') or value
            if value:
                options.append({'value': value, 'label': label})
    return options


@router.get('/account/profile', response=AccountProfileSchema, auth=jwt_auth_any_role)
def account_profile(request):
    user = request.user
    display_name = user.full_name
    email = user.email
    current_timezone = None
    timezone_options = []
    access_token = get_tpms_access_token(user.id)

    if access_token:
        try:
            personal_info = _tpms_unwrap(get_ios_personal_info(access_token))
            first_name = _tpms_value(personal_info, 'employee_first_name', 'first_name', 'firstName')
            middle_name = _tpms_value(personal_info, 'employee_middle_name', 'middle_name', 'middleName')
            last_name = _tpms_value(personal_info, 'employee_last_name', 'last_name', 'lastName')
            display_name = (
                _tpms_value(
                    personal_info,
                    'employee_nickname',
                    'display_name',
                    'displayName',
                    'employee_full_name',
                    'full_name',
                    'fullName',
                    'name',
                )
                or f'{first_name} {middle_name} {last_name}'.strip()
                or display_name
            )
            email = _tpms_value(personal_info, 'employee_email', 'email', 'login_email', 'loginEmail') or email
            current_timezone = _tpms_value(
                personal_info,
                'employee_timezone',
                'timezone',
                'time_zone',
            ) or None
        except TpmsAuthError as exc:
            if exc.status_code in {401, 403}:
                raise HttpError(401, 'TherapyPMS session expired. Please log in again.') from exc
            raise HttpError(502, str(exc) or 'Failed to load personal info') from exc

        try:
            timezone_payload = get_ios_time_zone(access_token)
            timezone_data = _tpms_unwrap(timezone_payload)
            current_timezone = current_timezone or _tpms_value(
                timezone_data,
                'timezone',
                'selected_timezone',
                'selectedTimezone',
                'value',
            ) or None
            timezone_options = _timezone_options(timezone_payload)
        except TpmsAuthError as exc:
            if exc.status_code in {401, 403}:
                raise HttpError(401, 'TherapyPMS session expired. Please log in again.') from exc
            raise HttpError(502, str(exc) or 'Failed to load timezone') from exc

    if current_timezone and not any(option['value'] == current_timezone for option in timezone_options):
        timezone_options.insert(0, {'value': current_timezone, 'label': current_timezone})

    return {
        'display_name': display_name,
        'email': email,
        'timezone': current_timezone,
        'timezone_options': timezone_options,
    }


def _same_practice_q(user: User, prefix: str = '') -> Q:
    """
    Scopes a queryset to users in the same practice as `user` — either the
    same TPMS external_admin_id (externally-linked users) or the same
    Organization (native users). `prefix` lets this reach through a related
    field, e.g. _same_practice_q(user, 'created_by__') for APIKey.

    external_admin_id must be checked before organization_id: one Organization
    can front several TPMS practices at once (see tenants.OrganizationTpmsAdminId),
    so a TPMS-linked user's organization_id alone doesn't identify their practice
    — checking it first would match every other practice sharing that org too.

    User/APIKey live in SHARED_APPS — one global table for every tenant on
    the platform, not schema-isolated — so without this filter, these
    admin-only endpoints would read/modify another tenant's (or practice's)
    users or keys given nothing more than a role check and a guessable id.
    """
    if user.external_admin_id is not None:
        return Q(**{f'{prefix}external_admin_id': user.external_admin_id})
    if user.organization_id is not None:
        return Q(**{f'{prefix}organization_id': user.organization_id})
    # Neither identifier set — scope to nothing rather than risk matching
    # every other user who also happens to have both fields null.
    return Q(**{f'{prefix}pk': None})


@router.get('/users', response=list[UserSchema], auth=jwt_auth)
def list_users(request):
    if not request.user.has_role('admin', 'supervisor'):
        raise HttpError(403, 'Insufficient permissions')
    return list(
        User.objects.filter(_same_practice_q(request.user), is_active=True)
        .exclude(role=User.Role.CAREGIVER)
        .select_related('mfa')
        .order_by('last_name', 'first_name')
    )


def _latest_password_links(user_ids: list[int]) -> dict[int, PasswordSetLink]:
    """Most recent PasswordSetLink per user, in one query."""
    latest: dict[int, PasswordSetLink] = {}
    for link in PasswordSetLink.objects.filter(user_id__in=user_ids).order_by('user_id', '-created_at'):
        latest.setdefault(link.user_id, link)
    return latest


def _password_link_status(user: User, link: PasswordSetLink | None) -> tuple[str, datetime | None]:
    """Computed, not stored — see StaffSchema.password_link_status."""
    if user.last_login is not None:
        return 'active', None
    if link is None:
        return 'not_invited', None
    if link.used_at is None and link.expires_at > timezone.now():
        return 'invited', link.expires_at
    return 'expired', link.expires_at


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

    qs = User.objects.filter(external_admin_id=request.user.external_admin_id).exclude(role=User.Role.CAREGIVER).select_related('mfa')
    if not include_inactive:
        qs = qs.filter(is_active=True)
    users = list(qs.order_by('last_name', 'first_name'))
    links = _latest_password_links([u.id for u in users])
    results = []
    for u in users:
        status, expires_at = _password_link_status(u, links.get(u.id))
        results.append(StaffSchema(
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
            role=u.role,
            mfa_required=u.mfa_required,
            mfa_enabled=u.mfa_enabled,
            password_link_status=status,
            password_link_expires_at=expires_at,
        ))
    return results


def _list_native_staffs(request, include_inactive: bool) -> list[StaffSchema]:
    """Native (non-TPMS) equivalent of list_admin_staffs — lists local Users
    bound to this admin's Organization instead of TPMS employees."""
    if request.user.organization_id is None:
        return []
    qs = User.objects.filter(organization_id=request.user.organization_id).select_related('mfa')
    if not include_inactive:
        qs = qs.filter(is_active=True)
    users = list(qs.order_by('last_name', 'first_name'))
    links = _latest_password_links([u.id for u in users])
    results = []
    for u in users:
        status, expires_at = _password_link_status(u, links.get(u.id))
        results.append(StaffSchema(
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
            role=u.role,
            mfa_required=u.mfa_required,
            mfa_enabled=u.mfa_enabled,
            password_link_status=status,
            password_link_expires_at=expires_at,
        ))
    return results


@router.post('/users/{user_id}/password-link', response=PasswordSetLinkGenerateResponse, auth=jwt_auth)
def generate_password_set_link(request, user_id: int, data: PasswordSetLinkGenerateRequest):
    """Admin-generated one-time link so a synced provider can set their DCM
    login password, instead of relying on TherapyPMS's /ios/login. See
    PasswordSetLink for why generating a new link expires any prior one."""
    require_permission(request, 'admin_users_edit')
    try:
        user = User.objects.get(_same_practice_q(request.user), id=user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')
    if user.role == User.Role.CAREGIVER:
        raise HttpError(400, 'Caregiver accounts sign in through the client portal, not this flow.')
    if data.expires_at <= timezone.now():
        raise HttpError(400, 'Expiration must be in the future')

    link, raw_token = PasswordSetLink.generate(user, created_by=request.user, expires_at=data.expires_at)
    base_url = getattr(settings, 'FRONTEND_BASE_URL', '').rstrip('/')
    url = f'{base_url}/set-password?token={raw_token}'
    return PasswordSetLinkGenerateResponse(url=url, expires_at=link.expires_at)


@router.post('/password-link/set-password', response=LoginResponse, auth=None)
def submit_password_set_link(request, data: PasswordSetLinkSubmitRequest):
    """Registered before GET /password-link/{token} below — Ninja/Django
    tries url patterns in registration order and the {token} converter
    matches any string including the literal "set-password", so this route
    must come first or POSTs here 405 against the {token} route instead."""
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise HttpError(401, 'Invalid or expired link')
    if data.password != data.password_confirm:
        raise HttpError(400, 'Passwords do not match')

    if not data.token.startswith('pwl_'):
        raise HttpError(404, 'This link is invalid or has expired')
    token_hash = hashlib.sha256(data.token.encode()).hexdigest()

    with transaction.atomic():
        # select_for_update + a fresh used_at/expires_at check inside the
        # transaction — not just PasswordSetLink.verify() up front — so two
        # concurrent requests for the same token can't both pass the check
        # and both consume it.
        link = (
            PasswordSetLink.objects.select_for_update()
            .select_related('user')
            .filter(token_hash=token_hash)
            .first()
        )
        if link is None or link.used_at is not None or link.expires_at < timezone.now():
            raise HttpError(404, 'This link is invalid or has expired')

        user = link.user
        user.set_password(data.password)
        user.save(update_fields=['password'])
        link.used_at = timezone.now()
        link.save(update_fields=['used_at'])

    return _mfa_gate(tenant, _issue_tokens(user, tenant.pk))


@router.get('/password-link/{token}', response=PasswordSetLinkInfoResponse, auth=None)
def get_password_set_link(request, token: str):
    link = PasswordSetLink.verify(token)
    if link is None:
        raise HttpError(404, 'This link is invalid or has expired')
    return PasswordSetLinkInfoResponse(
        first_name=link.user.first_name,
        email_masked=mfa.mask_email(link.user.email),
        expires_at=link.expires_at,
    )


_ROLE_RANK = {
    User.Role.REPORTING: 0,
    User.Role.STAFF: 0,
    User.Role.SUPERVISOR: 1,
    User.Role.ADMIN: 2,
}


def _assert_can_assign_role(request, target_role: str) -> None:
    if _ROLE_RANK.get(target_role, 0) > _ROLE_RANK.get(request.user.role, 0):
        raise HttpError(403, 'You cannot grant a role higher than your own')


@router.post('/users', response={201: UserSchema, 400: ErrorResponse}, auth=jwt_auth)
def create_user(request, data: UserCreateRequest):
    require_permission(request, 'admin_users_edit')
    if data.role == User.Role.CAREGIVER:
        return 400, ErrorResponse(detail='Caregiver accounts are provisioned by portal login only')
    _assert_can_assign_role(request, data.role)
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
    if user.role == User.Role.CAREGIVER or data.role == User.Role.CAREGIVER:
        raise HttpError(400, 'Caregiver accounts are provisioned and managed by portal login only')
    if data.role is not None:
        _assert_can_assign_role(request, data.role)
    for field, value in data.dict(exclude_none=True).items():
        setattr(user, field, value)
    user.save()
    return user


# Partner API keys are created, listed and revoked only by a platform
# superadmin — see apps.tenants.api ("/organization/superadmin/api-keys").
# There is deliberately no practice-admin-facing key management endpoint.


@router.get('/admin/logs', auth=jwt_auth)
def get_logs(request, limit: int = 200):
    if not request.user.has_role('admin'):
        raise HttpError(403, 'Admin access required')
    from shared.log_buffer import get_recent_logs
    return {'logs': get_recent_logs(limit)}


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
    # Caregiver access is identity-scoped (see apps.caregiver_portal), never
    # permission-key-scoped — exclude it even if a legacy RolePermission row
    # exists, so it can never resurface as an editable row in the Privileges UI.
    rows = RolePermission.objects.filter(organization=org).exclude(role=User.Role.CAREGIVER)
    for row in rows:
        merged = {**result.get(row.role, {}), **(row.permissions or {})}
        result[row.role] = _apply_role_guarantees(row.role, merged)
    result.pop(User.Role.CAREGIVER, None)
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

    # Caregiver is deliberately excluded — its access is identity-scoped
    # (apps.caregiver_portal), not permission-key-scoped, and must never
    # become toggleable via this admin page.
    valid_roles = {c[0] for c in User.Role.choices} - {User.Role.CAREGIVER}
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
