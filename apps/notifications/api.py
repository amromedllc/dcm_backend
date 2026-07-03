from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from .models import Notification

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
