from django.conf import settings
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from apps.accounts.auth import jwt_auth
from .models import FirebaseMessagingToken, Notification, NotificationPreference
from .preferences import PREFERENCE_TYPES
from .service import _send_firebase_push

router = Router(auth=jwt_auth)


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


class NotificationPreferenceUpdate(Schema):
    event_type: str
    email_enabled: bool = Field(...)
    web_enabled: bool = Field(...)


class NotificationPreferenceUpdateRequest(Schema):
    preferences: list[NotificationPreferenceUpdate]


class FirebaseTokenUpsert(Schema):
    token: str


class FirebaseTokenRemove(Schema):
    token: str


def _default_preferences_for_user(user) -> list[NotificationPreference]:
    existing = {
        pref.event_type: pref
        for pref in NotificationPreference.objects.filter(recipient=user)
    }
    preferences = []
    for event_type, _label in PREFERENCE_TYPES:
        pref = existing.get(event_type)
        if pref is None:
            pref = NotificationPreference.objects.create(
                recipient=user,
                event_type=event_type,
                email_enabled=True,
                web_enabled=True,
            )
        preferences.append(pref)
    return preferences


@router.get('/notifications/preferences', response=list[NotificationPreferenceSchema])
def list_notification_preferences(request):
    labels = dict(PREFERENCE_TYPES)
    return [
        {
            'event_type': pref.event_type,
            'label': labels[pref.event_type],
            'email_enabled': pref.email_enabled,
            'web_enabled': pref.web_enabled,
        }
        for pref in _default_preferences_for_user(request.user)
    ]


@router.put('/notifications/preferences', response=list[NotificationPreferenceSchema])
def update_notification_preferences(request, payload: NotificationPreferenceUpdateRequest):
    allowed = {event_type for event_type, _label in PREFERENCE_TYPES}
    for item in payload.preferences:
        if item.event_type not in allowed:
            raise HttpError(400, f'Unknown notification type: {item.event_type}')
        NotificationPreference.objects.update_or_create(
            recipient=request.user,
            event_type=item.event_type,
            defaults={
                'email_enabled': item.email_enabled,
                'web_enabled': item.web_enabled,
            },
        )
    return list_notification_preferences(request)


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
