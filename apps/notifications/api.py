from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from ninja import Body, Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from apps.accounts.auth import jwt_auth
from apps.accounts.models import User
from apps.accounts.permissions import require_permission, resolve_permission_organization, user_has_permission
from apps.clients.api import _get_accessible_clients
from apps.clients.models import Client, ClientStaffAssignment
from apps.integrations.tpms_auth_client import TpmsAuthError, get_tpms_access_token, list_providers
from apps.tenants.models import OrganizationTpmsAdminId
from .models import (
    FirebaseMessagingToken,
    Notification,
    NotificationPreference,
    RoleNotificationPolicy,
)
from .preferences import PREFERENCE_TYPES
from .service import _practice_email_notifications_enabled, _send_firebase_push

router = Router(auth=jwt_auth)

POLICY_ROLES = [User.Role.ADMIN, User.Role.SUPERVISOR, User.Role.STAFF]


def _require_report_edit_permission(request):
    organization = resolve_permission_organization(request)
    if not (
        user_has_permission(request.user, organization, 'client_report')
        or user_has_permission(request.user, organization, 'reports_add_edit')
    ):
        raise HttpError(403, 'Insufficient permissions')


def _role_policy_map(role: str) -> dict[str, dict]:
    """{event_type: {email_enabled, web_enabled, locked}} for a role, merged
    over the all-on / unlocked default."""
    result = {
        event_type: {'email_enabled': True, 'web_enabled': True, 'locked': False}
        for event_type, _label in PREFERENCE_TYPES
    }
    for row in RoleNotificationPolicy.objects.filter(role=role):
        if row.event_type in result:
            result[row.event_type] = {
                'email_enabled': row.email_enabled,
                'web_enabled': row.web_enabled,
                'locked': row.locked,
            }
    return result


class NotificationSchema(Schema):
    id: int
    event_type: str
    title: str
    body: str
    data: dict
    read_at: str | None
    created_at: str

    @staticmethod
    def resolve_read_at(obj):
        return obj.read_at.isoformat() if obj.read_at else None

    @staticmethod
    def resolve_created_at(obj):
        return obj.created_at.isoformat()


class NotificationPreferenceSchema(Schema):
    event_type: str
    label: str
    email_enabled: bool
    web_enabled: bool
    # True when an admin has locked this notification type for the user's role;
    # the toggles are read-only and the values below reflect the role policy.
    locked: bool = False
    email_available: bool = True


class NotificationPreferenceUpdate(Schema):
    event_type: str
    email_enabled: bool = Field(...)
    web_enabled: bool = Field(...)


class UserNotificationPreferenceUpdate(NotificationPreferenceUpdate):
    locked: bool = Field(...)


class NotificationPreferenceUpdateRequest(Schema):
    preferences: list[NotificationPreferenceUpdate]


class UserNotificationPreferenceUpdateRequest(Schema):
    preferences: list[UserNotificationPreferenceUpdate]


class FirebaseTokenUpsert(Schema):
    token: str


class FirebaseTokenRemove(Schema):
    token: str


class ReportReviewerSchema(Schema):
    id: int
    full_name: str
    email: str
    role: str


class PracticeEmailNotificationSettingSchema(Schema):
    admin_id: int
    email_notifications_enabled: bool


class NotificationUserSchema(Schema):
    id: int | None
    full_name: str
    email: str
    role: str
    external_employee_id: int | None = None
    manageable: bool = True


class ReportReviewRequestCreate(Schema):
    client_id: int
    report_key: str
    report_title: str
    reviewer_id: int
    url: str


class ReportReviewRequestComplete(Schema):
    report_key: str


def _default_preferences_for_user(user) -> list[NotificationPreference]:
    """Seed missing rows from the user's role policy (all-on when no policy)."""
    policy = _role_policy_map(getattr(user, 'role', ''))
    existing = {
        pref.event_type: pref
        for pref in NotificationPreference.objects.filter(recipient=user)
    }
    preferences = []
    for event_type, _label in PREFERENCE_TYPES:
        pref = existing.get(event_type)
        if pref is None:
            seed = policy.get(event_type, {})
            pref = NotificationPreference.objects.create(
                recipient=user,
                event_type=event_type,
                email_enabled=seed.get('email_enabled', True),
                web_enabled=seed.get('web_enabled', True),
            )
        preferences.append(pref)
    return preferences


def _effective_preference_rows(user, *, email_available: bool | None = None) -> list[dict]:
    labels = dict(PREFERENCE_TYPES)
    policy = _role_policy_map(getattr(user, 'role', ''))
    if email_available is None:
        email_available = _practice_email_notifications_enabled(user)

    rows = []
    for pref in _default_preferences_for_user(user):
        entry = policy.get(pref.event_type, {})
        has_user_lock_override = pref.locked is not None
        locked = bool(pref.locked) if has_user_lock_override else bool(entry.get('locked'))
        use_policy_value = locked and not has_user_lock_override
        rows.append({
            'event_type': pref.event_type,
            'label': labels[pref.event_type],
            'email_enabled': email_available and (entry['email_enabled'] if use_policy_value else pref.email_enabled),
            'web_enabled': entry['web_enabled'] if use_policy_value else pref.web_enabled,
            'locked': locked,
            'email_available': email_available,
        })
    return rows


@router.get('/notifications/preferences', response=list[NotificationPreferenceSchema])
def list_notification_preferences(request):
    return _effective_preference_rows(request.user)


@router.put('/notifications/preferences', response=list[NotificationPreferenceSchema])
def update_notification_preferences(request, payload: NotificationPreferenceUpdateRequest):
    allowed = {event_type for event_type, _label in PREFERENCE_TYPES}
    policy = _role_policy_map(getattr(request.user, 'role', ''))
    for item in payload.preferences:
        if item.event_type not in allowed:
            raise HttpError(400, f'Unknown notification type: {item.event_type}')
        entry = policy.get(item.event_type, {})
        pref = NotificationPreference.objects.filter(
            recipient=request.user,
            event_type=item.event_type,
        ).first()
        has_user_lock_override = pref is not None and pref.locked is not None
        locked = bool(pref.locked) if has_user_lock_override else bool(entry.get('locked'))
        email_enabled = item.email_enabled and _practice_email_notifications_enabled(request.user)
        if locked:
            # Ignore client-supplied values for locked types.
            continue
        NotificationPreference.objects.update_or_create(
            recipient=request.user,
            event_type=item.event_type,
            defaults={
                'email_enabled': email_enabled,
                'web_enabled': item.web_enabled,
            },
        )
    return list_notification_preferences(request)


def _manageable_notification_user(request, user_id: int) -> User:
    require_permission(request, 'admin_privileges')
    try:
        return User.objects.get(_notification_user_scope_q(request), id=user_id, is_active=True)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')


def _notification_user_scope_q(request) -> Q:
    if request.user.external_admin_id is not None:
        return Q(external_admin_id=request.user.external_admin_id)
    if request.user.organization_id is None:
        return Q(pk=None)

    mapped_admin_ids = OrganizationTpmsAdminId.objects.filter(
        organization_id=request.user.organization_id,
    ).values_list('admin_id', flat=True)
    return Q(organization_id=request.user.organization_id) | Q(external_admin_id__in=mapped_admin_ids)


def _notification_client_scope_q(request) -> Q:
    if request.user.external_admin_id is not None:
        return Q(external_admin_id=request.user.external_admin_id)
    if request.user.organization_id is None:
        return Q(pk=None)

    mapped_admin_ids = OrganizationTpmsAdminId.objects.filter(
        organization_id=request.user.organization_id,
    ).values_list('admin_id', flat=True)
    return Q(organization_id=request.user.organization_id) | Q(external_admin_id__in=mapped_admin_ids)


def _parse_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _notification_central_provider_rows(request) -> list[dict]:
    rows: list[dict] = []
    if request.user.external_admin_id is not None:
        token = get_tpms_access_token(request.user.id)
        if token:
            try:
                rows = list_providers(token)
            except TpmsAuthError:
                rows = []

    if not rows:
        rows = [
            {'id': client.external_id, 'name': client.full_name}
            for client in (
                Client.objects
                .filter(_notification_client_scope_q(request))
                .exclude(external_id='')
                .order_by('last_name', 'first_name')
            )
        ]

    seen = set()
    result = []
    for row in rows:
        external_id = row.get('id')
        if external_id is None:
            continue
        key = str(external_id)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            'external_employee_id': _parse_int(external_id),
            'external_employee_key': key,
            'full_name': str(row.get('name') or '').strip() or f'Provider {external_id}',
        })
    result.sort(key=lambda item: item['full_name'].lower())
    return result


@router.get('/notifications/users', response=list[NotificationUserSchema])
def list_notification_users(request):
    require_permission(request, 'admin_privileges')
    users = list(
        User.objects
        .filter(_notification_user_scope_q(request), is_active=True)
        .exclude(role=User.Role.CAREGIVER)
        .order_by('last_name', 'first_name', 'email')
    )
    users_by_employee_id = {
        str(user.external_employee_id): user
        for user in users
        if user.external_employee_id is not None
    }

    result = []
    added_user_ids = set()
    for provider in _notification_central_provider_rows(request):
        user = users_by_employee_id.get(provider['external_employee_key'])
        if user is not None:
            added_user_ids.add(user.id)
        result.append({
            'id': user.id if user else None,
            'full_name': (user.full_name or user.email) if user else provider['full_name'],
            'email': user.email if user else '',
            'role': user.role if user else User.Role.STAFF,
            'external_employee_id': provider['external_employee_id'],
            'manageable': user is not None,
        })

    for user in users:
        if user.id in added_user_ids:
            continue
        result.append({
            'id': user.id,
            'full_name': user.full_name or user.email,
            'email': user.email,
            'role': user.role,
            'external_employee_id': user.external_employee_id,
            'manageable': True,
        })

    return result


@router.get('/notifications/user-preferences', response=list[NotificationPreferenceSchema])
def list_user_notification_preferences(request, user_id: int):
    user = _manageable_notification_user(request, user_id)
    return _effective_preference_rows(user)


@router.put('/notifications/user-preferences/{user_id}', response=list[NotificationPreferenceSchema])
def save_user_notification_preferences(request, user_id: int, payload: UserNotificationPreferenceUpdateRequest):
    user = _manageable_notification_user(request, user_id)
    allowed = {event_type for event_type, _label in PREFERENCE_TYPES}
    email_available = _practice_email_notifications_enabled(user)

    for item in payload.preferences:
        if item.event_type not in allowed:
            raise HttpError(400, f'Unknown notification type: {item.event_type}')

    for item in payload.preferences:
        NotificationPreference.objects.update_or_create(
            recipient=user,
            event_type=item.event_type,
            defaults={
                'email_enabled': item.email_enabled and email_available,
                'web_enabled': item.web_enabled,
                'locked': item.locked,
            },
        )
    return _effective_preference_rows(user, email_available=email_available)


@router.get('/notifications/practice-email-settings', response=list[PracticeEmailNotificationSettingSchema])
def list_practice_email_notification_settings(request):
    require_permission(request, 'admin_privileges')
    org = resolve_permission_organization(request)
    return list(
        OrganizationTpmsAdminId.objects.filter(organization=org)
        .order_by('admin_id')
        .values('admin_id', 'email_notifications_enabled')
    )


@router.get('/notifications/role-policies')
def get_role_notification_policies(request):
    """Full matrix as {role: {event_type: {email_enabled, web_enabled, locked}}}.

    Facility-scoped (current tenant). Requires the privileges permission.
    """
    require_permission(request, 'admin_privileges')
    return {role: _role_policy_map(role) for role in POLICY_ROLES}


@router.put('/notifications/role-policies')
def save_role_notification_policies(request, body: dict = Body(...)):
    """Persist the matrix. Body: {role: {event_type: {email_enabled, web_enabled, locked}}}."""
    require_permission(request, 'admin_privileges')
    org = resolve_permission_organization(request)
    allowed_types = {event_type for event_type, _label in PREFERENCE_TYPES}
    valid_roles = set(POLICY_ROLES)
    current_policy = {role: _role_policy_map(role) for role in POLICY_ROLES}

    for role, entries in body.items():
        if role not in valid_roles:
            raise HttpError(400, f'Invalid role: {role}')
        if not isinstance(entries, dict):
            raise HttpError(400, f'Entries must be an object for role {role}')
        for event_type, vals in entries.items():
            if event_type not in allowed_types:
                raise HttpError(400, f'Unknown notification type: {event_type}')
            if not isinstance(vals, dict):
                raise HttpError(400, f'Entry must be an object for {role}/{event_type}')

    with transaction.atomic():
        for role, entries in body.items():
            for event_type, vals in entries.items():
                email_enabled = bool(vals.get('email_enabled', True))
                web_enabled = bool(vals.get('web_enabled', True))
                previous = current_policy[role][event_type]

                if (
                    previous['email_enabled'] != email_enabled
                    or previous['web_enabled'] != web_enabled
                ):
                    NotificationPreference.objects.filter(
                        recipient__organization=org,
                        recipient__role=role,
                        event_type=event_type,
                        locked__isnull=True,
                    ).update(
                        email_enabled=email_enabled,
                        web_enabled=web_enabled,
                    )

                RoleNotificationPolicy.objects.update_or_create(
                    role=role,
                    event_type=event_type,
                    defaults={
                        'email_enabled': email_enabled,
                        'web_enabled': web_enabled,
                        'locked': bool(vals.get('locked', False)),
                    },
                )

    return {role: _role_policy_map(role) for role in POLICY_ROLES}


@router.post('/notifications/firebase-tokens', response={200: dict})
def save_firebase_token(request, payload: FirebaseTokenUpsert):
    FirebaseMessagingToken.objects.update_or_create(
        token=payload.token,
        defaults={
            'recipient': request.user,
            'user_agent': request.headers.get('User-Agent', ''),
            'is_active': True,
        },
    )
    return {'ok': True}


@router.post('/notifications/firebase-tokens/delete', response={200: dict})
def delete_firebase_token(request, payload: FirebaseTokenRemove):
    updated = FirebaseMessagingToken.objects.filter(
        recipient=request.user,
        token=payload.token,
    ).update(is_active=False)
    return {'updated': updated}


@router.post('/notifications/test-push', response={200: dict})
def send_test_push(request):
    if not FirebaseMessagingToken.all_organizations.filter(recipient=request.user, is_active=True).exists():
        raise HttpError(400, 'No active web push registration for your account. Enable web push first.')
    _send_firebase_push(
        request.user,
        title='Test notification',
        body='If you can see this, web push is working.',
        data={},
    )
    return {'ok': True}


@router.get('/notifications', response=list[NotificationSchema])
def list_notifications(request, unread_only: bool = False):
    qs = Notification.objects.filter(recipient=request.user)
    if unread_only:
        qs = qs.filter(read_at__isnull=True)
    return list(qs[:60])


@router.get('/notifications/report-reviewers', response=list[ReportReviewerSchema])
def list_report_reviewers(request, client_id: int):
    _require_report_edit_permission(request)
    try:
        client = _get_accessible_clients(request).get(id=client_id)
    except Exception:
        raise HttpError(404, 'Client not found')

    user_ids = set()
    org_id = getattr(client, 'organization_id', None)
    if org_id:
        user_ids.update(
            User.objects.filter(
                organization_id=org_id,
                role__in=[User.Role.ADMIN, User.Role.SUPERVISOR],
                is_active=True,
            ).values_list('id', flat=True)
        )

    user_ids.update(
        ClientStaffAssignment.objects
        .filter(client=client, is_active=True, user__is_active=True)
        .values_list('user_id', flat=True)
    )

    if client.external_admin_id is not None:
        user_ids.update(
            User.objects.filter(
                external_admin_id=client.external_admin_id,
                role__in=[User.Role.ADMIN, User.Role.SUPERVISOR, User.Role.STAFF],
                is_active=True,
            ).values_list('id', flat=True)
        )

    caregiver_filters = Q(role=User.Role.CAREGIVER, is_active=True)
    caregiver_ids = [client.id]
    if client.external_id:
        try:
            caregiver_ids.append(int(client.external_id))
        except (TypeError, ValueError):
            pass
    user_ids.update(
        User.objects.filter(caregiver_filters, external_client_id__in=caregiver_ids)
        .values_list('id', flat=True)
    )

    rows = []
    for user in User.objects.filter(id__in=user_ids, is_active=True).order_by('first_name', 'last_name', 'email'):
        rows.append({
            'id': user.id,
            'full_name': user.full_name or user.email,
            'email': user.email,
            'role': user.role,
        })
    return rows


@router.post('/notifications/report-review-requests', response=dict)
def send_report_review_request(request, payload: ReportReviewRequestCreate):
    _require_report_edit_permission(request)
    reviewers = list_report_reviewers(request, payload.client_id)
    if payload.reviewer_id not in {reviewer['id'] for reviewer in reviewers}:
        raise HttpError(400, 'Reviewer does not have access to this client')

    notification = Notification.objects.create(
        recipient_id=payload.reviewer_id,
        event_type=Notification.EventType.REPORT_REVIEW_REQUEST,
        title='Review report',
        body=f'{request.user.full_name or request.user.email} sent "{payload.report_title}" for review.',
        data={
            'client_id': payload.client_id,
            'report_key': payload.report_key,
            'report_title': payload.report_title,
            'url': payload.url,
            'status': 'action_required',
        },
    )
    from .service import _send_firebase_push
    _send_firebase_push(notification.recipient, notification.title, notification.body, notification.data)
    return {'notification_id': notification.id}


@router.post('/notifications/report-review-requests/complete', response=dict)
def complete_report_review_request(request, payload: ReportReviewRequestComplete):
    updated = Notification.objects.filter(
        event_type=Notification.EventType.REPORT_REVIEW_REQUEST,
        data__report_key=payload.report_key,
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    return {'updated': updated}


@router.patch('/notifications/{notification_id}/read', response=NotificationSchema)
def mark_read(request, notification_id: int):
    try:
        n = Notification.objects.get(id=notification_id, recipient=request.user)
    except Notification.DoesNotExist:
        raise HttpError(404, 'Notification not found')
    if not n.read_at:
        n.read_at = timezone.now()
        n.save(update_fields=['read_at'])
    return n


@router.post('/notifications/mark-all-read', response={200: dict})
def mark_all_read(request):
    updated = Notification.objects.filter(recipient=request.user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return {'updated': updated}


@router.get('/notifications/unread-count', response=dict)
def unread_count(request):
    count = Notification.objects.filter(recipient=request.user, read_at__isnull=True).count()
    return {'count': count}
