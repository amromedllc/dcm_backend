from django.conf import settings
from django.db import models
from shared.models import TenantAwareModel


class Notification(TenantAwareModel):
    class EventType(models.TextChoices):
        SESSION_SUBMITTED   = 'session_submitted',   'Session submitted for review'
        SESSION_APPROVED    = 'session_approved',     'Session approved'
        SESSION_REJECTED    = 'session_rejected',     'Session rejected'
        TARGET_ADVANCED     = 'target_advanced',      'Target automatically advanced'

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
        db_constraint=False,
    )
    event_type = models.CharField(max_length=40, choices=EventType.choices, db_index=True)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    data = models.JSONField(default=dict)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'notifications'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.event_type} → {self.recipient_id}'
