from django.conf import settings
from django.db import models
from shared.models import TenantAwareModel


class Notification(TenantAwareModel):
    class EventType(models.TextChoices):
        SESSION_SUBMITTED   = 'session_submitted',   'Session submitted for review'
        SESSION_APPROVED    = 'session_approved',     'Session approved'
        SESSION_REJECTED    = 'session_rejected',     'Session rejected'
        TARGET_ADVANCED     = 'target_advanced',      'Target automatically advanced'
        TARGET_PROMPT_LEVEL_CHANGED = 'target_prompt_level_changed', 'Target prompt level automatically changed'

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


class NotificationPreference(TenantAwareModel):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_preferences',
        db_constraint=False,
    )
    event_type = models.CharField(max_length=80, db_index=True)
    email_enabled = models.BooleanField(default=True)
    web_enabled = models.BooleanField(default=True)

    class Meta:
        app_label = 'notifications'
        unique_together = [('recipient', 'event_type')]
        ordering = ['event_type']

    def __str__(self) -> str:
        return f'{self.event_type} preferences for {self.recipient_id}'


class FirebaseMessagingToken(TenantAwareModel):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='firebase_messaging_tokens',
        db_constraint=False,
    )
    token = models.TextField(unique=True)
    user_agent = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'notifications'
        ordering = ['-updated_at']

    def __str__(self) -> str:
        return f'firebase token for {self.recipient_id}'
