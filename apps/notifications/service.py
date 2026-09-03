"""
Thin helpers to create Notification rows. Called from sessions and programs services.
All failures are swallowed — notifications must never break the primary flow.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


def _create(recipient_id: int, event_type: str, title: str, body: str = '', data: dict | None = None):
    try:
        from apps.accounts.models import User
        recipient = User.objects.get(id=recipient_id)
        if _channel_enabled(recipient, event_type, 'web'):
            from .models import Notification
            Notification.objects.create(
                recipient=recipient,
                event_type=event_type,
                title=title,
                body=body,
                data=data or {},
            )
            _send_firebase_push(recipient, title, body, data or {})
        if _channel_enabled(recipient, event_type, 'email'):
            _send_email(recipient.email, title, body)
    except Exception:
        logger.exception('Failed to create notification (event=%s recipient=%s)', event_type, recipient_id)


def _channel_enabled(recipient, event_type: str, channel: str) -> bool:
    try:
        from .models import NotificationPreference
        from .preferences import preference_key_for_event
        preference_event_type = preference_key_for_event(event_type)
        pref = NotificationPreference.all_organizations.filter(
            recipient=recipient,
            event_type=preference_event_type,
        ).first()
        if pref is None:
            return True
        return pref.email_enabled if channel == 'email' else pref.web_enabled
    except Exception:
        logger.exception('Failed to resolve notification preference (event=%s recipient=%s)', event_type, recipient.id)
        return True


def _send_email(email: str, subject: str, body: str):
    if not email:
        return
    try:
        send_mail(
            subject=subject,
            message=body or subject,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Failed to send notification email to %s', email)


def _firebase_app():
    if not getattr(settings, 'FIREBASE_PROJECT_ID', '') and not getattr(settings, 'FIREBASE_CREDENTIALS_JSON', ''):
        return None
    try:
        import json
        import firebase_admin
        from firebase_admin import credentials
    except Exception:
        logger.exception('firebase-admin is not installed; cannot send Firebase push')
        return None

    if firebase_admin._apps:
        return firebase_admin.get_app()

    try:
        if getattr(settings, 'FIREBASE_CREDENTIALS_JSON', ''):
            info = json.loads(settings.FIREBASE_CREDENTIALS_JSON)
        else:
            info = {
                'type': 'service_account',
                'project_id': settings.FIREBASE_PROJECT_ID,
                'private_key': settings.FIREBASE_PRIVATE_KEY.replace('\\n', '\n'),
                'client_email': settings.FIREBASE_CLIENT_EMAIL,
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        return firebase_admin.initialize_app(credentials.Certificate(info))
    except Exception:
        logger.exception('Failed to initialize Firebase Admin')
        return None


def _send_firebase_push(recipient, title: str, body: str, data: dict):
    app = _firebase_app()
    if app is None:
        return
    try:
        from firebase_admin import exceptions as firebase_exceptions
        from firebase_admin import messaging
        from .models import FirebaseMessagingToken
    except Exception:
        logger.exception('firebase-admin messaging is unavailable')
        return

    url = _notification_url(data)
    tokens = FirebaseMessagingToken.all_organizations.filter(recipient=recipient, is_active=True)
    for token in tokens:
        try:
            messaging.send(
                messaging.Message(
                    token=token.token,
                    data={'title': title, 'body': body, 'url': url},
                ),
                app=app,
            )
            token.last_sent_at = timezone.now()
            token.save(update_fields=['last_sent_at', 'updated_at'])
        except Exception as exc:
            if isinstance(exc, (messaging.UnregisteredError, firebase_exceptions.InvalidArgumentError)):
                token.is_active = False
                token.save(update_fields=['is_active', 'updated_at'])
            else:
                logger.exception('Failed to send Firebase push to token %s', token.id)


def _notification_url(data: dict) -> str:
    client_id = data.get('client_id')
    session_id = data.get('session_id')
    if client_id and session_id:
        return f'/clients/{client_id}/sessions'
    if client_id:
        return f'/clients/{client_id}'
    return '/dashboard'


def _client_display_name(client_id: int | None) -> str:
    """SessionRun.external_client_id is the local clients.Client row's own
    pk — resolve it to a name for notification text instead of showing the
    raw id, which means nothing to the person reading the notification."""
    if not client_id:
        return 'a client'
    try:
        from apps.clients.models import Client
        return Client.objects.get(id=client_id).full_name
    except Exception:
        return 'a client'


def notify_session_submitted(session_run):
    """Notify all admins/supervisors that a session needs review."""
    from apps.accounts.models import User
    reviewers = User.objects.filter(role__in=['admin', 'supervisor'])
    client_id = session_run.external_client_id
    client_name = _client_display_name(client_id)
    staff_name = f'{session_run.staff.first_name} {session_run.staff.last_name}'.strip() if session_run.staff else 'Staff'
    for reviewer in reviewers:
        _create(
            recipient_id=reviewer.id,
            event_type='session_submitted',
            title='Session submitted for review',
            body=f'{staff_name} submitted a session for {client_name}.',
            data={'session_id': session_run.id, 'client_id': client_id},
        )


def notify_session_approved(session_run):
    """Notify the staff member their session was approved."""
    if not session_run.staff_id:
        return
    client_name = _client_display_name(session_run.external_client_id)
    _create(
        recipient_id=session_run.staff_id,
        event_type='session_approved',
        title='Session approved',
        body=f'Your session for {client_name} has been approved.',
        data={'session_id': session_run.id, 'client_id': session_run.external_client_id},
    )


def notify_session_rejected(session_run):
    """Notify the staff member their session was rejected."""
    if not session_run.staff_id:
        return
    client_name = _client_display_name(session_run.external_client_id)
    _create(
        recipient_id=session_run.staff_id,
        event_type='session_rejected',
        title='Session rejected',
        body=f'Your session for {client_name} was rejected: {session_run.rejection_reason}',
        data={'session_id': session_run.id, 'client_id': session_run.external_client_id,
              'reason': session_run.rejection_reason},
    )


def notify_session_attachment_added(media):
    """Notify admins/supervisors that a file/photo/video was attached to a session."""
    from apps.accounts.models import User
    session_run = media.session_run
    reviewers = User.objects.filter(role__in=['admin', 'supervisor']).exclude(id=media.created_by_id)
    client_name = _client_display_name(session_run.external_client_id)
    for reviewer in reviewers:
        _create(
            recipient_id=reviewer.id,
            event_type='session_attachment_added',
            title='New session attachment',
            body=f'A {media.media_type} was attached to a session for {client_name}.',
            data={
                'session_id': session_run.id,
                'client_id': session_run.external_client_id,
                'media_id': media.id,
            },
        )


def notify_file_upload_failed(session_run, media_type: str, uploader):
    """Notify admins/supervisors that a file/photo/video failed to upload."""
    from apps.accounts.models import User
    reviewers = User.objects.filter(role__in=['admin', 'supervisor']).exclude(id=getattr(uploader, 'id', None))
    client_name = _client_display_name(session_run.external_client_id)
    uploader_name = f'{uploader.first_name} {uploader.last_name}'.strip() if uploader else 'A staff member'
    for reviewer in reviewers:
        _create(
            recipient_id=reviewer.id,
            event_type='file_upload_failed',
            title='File upload failed',
            body=f'{uploader_name} tried to upload a {media_type} for {client_name} but it failed.',
            data={'session_id': session_run.id, 'client_id': session_run.external_client_id},
        )


def notify_target_advanced(target, session_run):
    """Notify admins/supervisors that a target auto-advanced."""
    from apps.accounts.models import User
    reviewers = User.objects.filter(role__in=['admin', 'supervisor'])
    for reviewer in reviewers:
        _create(
            recipient_id=reviewer.id,
            event_type='target_advanced',
            title=f'Target advanced: {target.name}',
            body=f'"{target.name}" advanced to {target.status} automatically.',
            data={
                'target_id': target.id,
                'target_name': target.name,
                'new_status': target.status,
                'session_id': session_run.id,
            },
        )


def notify_target_mastered(target, session_run):
    """Notify admins/supervisors that a target reached mastery."""
    from apps.accounts.models import User
    reviewers = User.objects.filter(role__in=['admin', 'supervisor'])
    for reviewer in reviewers:
        _create(
            recipient_id=reviewer.id,
            event_type='target_mastered',
            title=f'Target mastered: {target.name}',
            body=f'"{target.name}" has been mastered.',
            data={
                'target_id': target.id,
                'target_name': target.name,
                'session_id': session_run.id,
            },
        )


_TERMINAL_TARGET_STATUS_KEYS = {'mastered', 'closed', 'discontinued', 'hold'}


def notify_target_reopened(target, old_status: str, new_status: str, reviewer):
    """Notify admins/supervisors that a target moved back to active status
    after being mastered/closed/discontinued/on hold."""
    from apps.accounts.models import User
    reviewers = User.objects.filter(role__in=['admin', 'supervisor']).exclude(id=getattr(reviewer, 'id', None))
    for r in reviewers:
        _create(
            recipient_id=r.id,
            event_type='target_reopened',
            title=f'Target reopened: {target.name}',
            body=f'"{target.name}" was moved from {old_status} back to {new_status}.',
            data={
                'target_id': target.id,
                'target_name': target.name,
                'old_status': old_status,
                'new_status': new_status,
            },
        )


def notify_program_modified(program, editor):
    """Notify admins/supervisors (other than the editor) that a program was modified."""
    from apps.accounts.models import User
    reviewers = User.objects.filter(role__in=['admin', 'supervisor']).exclude(id=getattr(editor, 'id', None))
    for reviewer in reviewers:
        _create(
            recipient_id=reviewer.id,
            event_type='program_modified',
            title=f'Program modified: {program.name}',
            body=f'"{program.name}" was updated.',
            data={'program_id': program.id, 'program_name': program.name},
        )


def notify_signature_request(note):
    """Notify the client's caregiver that a note is awaiting their signature."""
    from apps.accounts.models import User
    caregivers = User.objects.filter(role='caregiver', external_client_id=note.external_client_id)
    for caregiver in caregivers:
        _create(
            recipient_id=caregiver.id,
            event_type='signature_request',
            title='Signature requested',
            body='A session note is ready for your signature.',
            data={'note_id': note.id, 'client_id': note.external_client_id},
        )


def notify_target_prompt_level_changed(target, session_run, direction: str, new_level_label: str):
    """Notify admins/supervisors that a target's prompt level auto-faded.

    direction: 'advanced' or 'regressed'.
    """
    from apps.accounts.models import User
    reviewers = User.objects.filter(role__in=['admin', 'supervisor'])
    for reviewer in reviewers:
        _create(
            recipient_id=reviewer.id,
            event_type='target_prompt_level_changed',
            title=f'Target prompt level {direction}: {target.name}',
            body=f'"{target.name}" {direction} to prompt level "{new_level_label}" automatically.',
            data={
                'target_id': target.id,
                'target_name': target.name,
                'direction': direction,
                'new_level_index': target.current_prompt_level_index,
                'new_level_label': new_level_label,
                'session_id': session_run.id,
            },
        )
