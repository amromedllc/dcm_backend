"""
Thin helpers to create Notification rows. Called from sessions and programs services.
All failures are swallowed — notifications must never break the primary flow.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
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
            _send_email(recipient.email, title, body, data or {})
    except Exception:
        logger.exception('Failed to create notification (event=%s recipient=%s)', event_type, recipient_id)


def _channel_enabled(recipient, event_type: str, channel: str) -> bool:
    try:
        from .models import NotificationPreference, RoleNotificationPolicy
        from .preferences import preference_key_for_event
        preference_event_type = preference_key_for_event(event_type)

        if channel == 'email' and not _practice_email_notifications_enabled(recipient):
            return False

        def channel_value(obj):
            return obj.email_enabled if channel == 'email' else obj.web_enabled

        policy = RoleNotificationPolicy.all_organizations.filter(
            organization_id=getattr(recipient, 'organization_id', None),
            role=getattr(recipient, 'role', ''),
            event_type=preference_event_type,
        ).first()
        pref = NotificationPreference.all_organizations.filter(
            organization_id=getattr(recipient, 'organization_id', None),
            recipient=recipient,
            event_type=preference_event_type,
        ).first()

        # An admin-managed user preference is explicit and wins over role defaults.
        if pref is not None and pref.locked is not None:
            return channel_value(pref)
        # A locked role policy overrides ordinary personal preferences.
        if policy is not None and policy.locked:
            return channel_value(policy)
        if pref is not None:
            return channel_value(pref)
        if policy is not None:
            return channel_value(policy)
        return True
    except Exception:
        logger.exception('Failed to resolve notification preference (event=%s recipient=%s)', event_type, recipient.id)
        return True


def _practice_email_notifications_enabled(recipient) -> bool:
    admin_id = getattr(recipient, 'external_admin_id', None)
    if admin_id is None:
        return True
    try:
        from apps.tenants.models import OrganizationTpmsAdminId
        return OrganizationTpmsAdminId.objects.filter(
            organization_id=getattr(recipient, 'organization_id', None),
            admin_id=admin_id,
            email_notifications_enabled=True,
        ).exists()
    except Exception:
        logger.exception('Failed to resolve practice email notification gate for recipient=%s', recipient.id)
        return False


def _send_email(email: str, subject: str, body: str, data: dict | None = None):
    if not email:
        return
    try:
        base_url = getattr(settings, 'FRONTEND_BASE_URL', '').rstrip('/')
        cta_url = f'{base_url}{_notification_url(data or {})}' if base_url else None
        html_body = render_to_string('notifications/email_notification.html', {
            'app_name': 'Progressly',
            'title': subject,
            'body': body,
            'cta_url': cta_url,
            'preferences_url': f'{base_url}/account?section=notifications' if base_url else None,
        })
        message = EmailMultiAlternatives(
            subject=subject,
            body=body or subject,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            to=[email],
        )
        message.attach_alternative(html_body, 'text/html')
        message.send(fail_silently=False)
    except Exception:
        logger.exception('Failed to send notification email to %s', email)


def send_password_set_invite(email: str, first_name: str, url: str, expires_at) -> None:
    """Send the dedicated, branded password setup invitation email."""
    if not email:
        return
    from django.utils import timezone

    expiry = timezone.localtime(expires_at).strftime('%B %-d, %Y at %-I:%M %p %Z')
    subject = 'Set up your Progressly account'
    body = (
        f"Hi {first_name or 'there'},\n\n"
        'You have been invited to set up your Progressly account. Use the link below to choose your password. '
        f'This link expires {expiry}.\n\n'
        f'{url}\n\nIf you were not expecting this invitation, you can ignore this email.'
    )
    try:
        html_body = render_to_string('notifications/password_set_invite.html', {
            'first_name': first_name,
            'setup_url': url,
            'expiry': expiry,
        })
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            to=[email],
        )
        message.attach_alternative(html_body, 'text/html')
        message.send(fail_silently=False)
    except Exception:
        logger.exception('Failed to send password setup invitation to %s', email)


def send_password_reset_email(email: str, first_name: str, url: str, expires_at) -> None:
    """Send the branded self-service password-reset email. Failure is
    swallowed, same as send_password_set_invite above — the request endpoint
    always returns a generic response regardless of whether sending
    succeeded, so a client can't tell from this function's behavior whether
    the address it submitted matched an account."""
    if not email:
        return
    from django.utils import timezone

    expiry = timezone.localtime(expires_at).strftime('%B %-d, %Y at %-I:%M %p %Z')
    subject = 'Reset your Progressly password'
    body = (
        f"Hi {first_name or 'there'},\n\n"
        'We received a request to reset your Progressly password. Use the link below to choose a new one. '
        f'This link expires {expiry}.\n\n'
        f'{url}\n\nIf you did not request this, you can ignore this email — your password will not be changed.'
    )
    try:
        html_body = render_to_string('notifications/password_reset.html', {
            'first_name': first_name,
            'reset_url': url,
            'expiry': expiry,
        })
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            to=[email],
        )
        message.attach_alternative(html_body, 'text/html')
        message.send(fail_silently=False)
    except Exception:
        logger.exception('Failed to send password reset email to %s', email)


def send_mfa_verification_code(email: str, first_name: str, code: str, expires_in_minutes: int) -> None:
    """Send a branded email containing a one-time MFA verification code."""
    if not email:
        raise ValueError('Cannot send an MFA verification code without a recipient email address')
    subject = 'Your Progressly verification code'
    body = (
        f"Hi {first_name or 'there'},\n\n"
        f'Your verification code is {code}. It expires in {expires_in_minutes} minutes. '
        'If you did not request this, you can ignore this email.'
    )
    try:
        html_body = render_to_string('notifications/mfa_verification_code.html', {
            'first_name': first_name,
            'code': code,
            'expires_in_minutes': expires_in_minutes,
        })
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            to=[email],
        )
        message.attach_alternative(html_body, 'text/html')
        sent_count = message.send(fail_silently=False)
        if sent_count != 1:
            raise RuntimeError('Email backend did not send the MFA verification message')
    except Exception:
        logger.exception('Failed to send MFA verification email to %s', email)
        raise


_ROLE_LABEL = {
    'staff': 'Staff (therapist)',
    'supervisor': 'Supervisor',
    'admin': 'Administrator',
    'superadmin': 'Superadmin',
}


def send_assistant_ticket(user, transcript: list[dict], note: str = '') -> None:
    """Email an unresolved help-chat conversation to the support inbox as a
    branded ticket. Unlike _create()/_send_email() above, failures are NOT
    swallowed — the caller (the /assistant/ticket endpoint) needs to tell
    the user whether it actually sent."""
    support_email = getattr(settings, 'SUPPORT_EMAIL', '')
    if not support_email:
        raise ValueError('SUPPORT_EMAIL is not configured.')

    user_name = user.full_name or '(no name)'
    org_name = getattr(getattr(user, 'organization', None), 'name', '') or ''
    role_label = _ROLE_LABEL.get(user.role, user.role)
    subject = f'[Support ticket] {user_name} — Progressly help chat'

    turns = [
        {
            'speaker': 'User' if message.get('role') == 'user' else 'Assistant',
            'is_user': message.get('role') == 'user',
            'content': message.get('content', ''),
        }
        for message in transcript
    ]

    # Plain-text fallback for clients that don't render HTML.
    text_lines = [f'{t["speaker"]}: {t["content"]}' for t in turns]
    body = '\n\n'.join([
        f'{user_name} <{user.email}>',
        f'Role: {role_label}' + (f' · {org_name}' if org_name else ''),
        *([f'Note: {note}'] if note else []),
        '--- Conversation ---',
        '\n'.join(text_lines),
    ])

    html_body = render_to_string('notifications/assistant_ticket.html', {
        'subject': subject,
        'user_name': user_name,
        'user_email': user.email,
        'role_label': role_label,
        'org_name': org_name,
        'note': note,
        'transcript': turns,
    })

    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=[support_email],
    )
    message.attach_alternative(html_body, 'text/html')
    sent_count = message.send(fail_silently=False)
    if sent_count != 1:
        raise RuntimeError('Email backend did not send the support ticket message')


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
    if data.get('url'):
        return str(data['url'])
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
        client = _resolve_local_client(client_id)
        return client.full_name if client else 'a client'
    except Exception:
        return 'a client'


def _reviewers_for(organization_id: int | None, *, exclude_user_id: int | None = None):
    """Admins/supervisors in the SAME organization as the event — never
    cross-notify another facility just because it shares a DB schema."""
    from apps.accounts.models import User
    qs = User.objects.filter(role__in=['admin', 'supervisor'], organization_id=organization_id)
    if exclude_user_id is not None:
        qs = qs.exclude(id=exclude_user_id)
    return qs


def _resolve_local_client(external_client_id: int | None):
    """external_client_id is used inconsistently across models — sometimes
    the local Client row's own pk (SessionRun), sometimes TPMS's real
    external id, sometimes either (Program, see _accessible_external_client_ids
    in apps/programs/api.py) — so try both rather than assuming one."""
    if not external_client_id:
        return None
    from apps.clients.models import Client
    return (
        Client.objects.filter(id=external_client_id).first()
        or Client.objects.filter(external_id=str(external_client_id)).first()
    )


def _assigned_staff_for(client, *, exclude_user_id: int | None = None):
    """Staff actively assigned to this specific client — the event's
    recipient pool for client-scoped notifications, never every
    admin/supervisor in the organization."""
    if client is None:
        return []
    from apps.clients.models import ClientStaffAssignment
    qs = ClientStaffAssignment.objects.filter(client=client, is_active=True).select_related('user')
    if exclude_user_id is not None:
        qs = qs.exclude(user_id=exclude_user_id)
    return [assignment.user for assignment in qs]


def notify_session_submitted(session_run):
    """Notify all admins/supervisors that a session needs review."""
    reviewers = _reviewers_for(session_run.organization_id)
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


def notify_appointment_reminder(appointment):
    """Notify the appointment's staff member shortly before it starts."""
    if not appointment.staff_id:
        return
    client_name = _client_display_name(appointment.external_client_id)
    start = timezone.localtime(appointment.start_time).strftime('%I:%M %p')
    _create(
        recipient_id=appointment.staff_id,
        event_type='appointment_reminder',
        title='Upcoming appointment',
        body=f'Your appointment with {client_name} starts at {start}.',
        data={
            'appointment_id': appointment.id,
            'client_id': appointment.external_client_id,
            'url': '/reporting/appointments',
        },
    )


def notify_appointment_changed(appointment, changed_fields, previous_staff_id=None):
    """Notify the old and new assigned staff member about schedule changes."""
    from apps.accounts.models import User

    recipient_ids = {uid for uid in (previous_staff_id, appointment.staff_id) if uid}
    if not recipient_ids:
        return
    recipients = User.objects.filter(id__in=recipient_ids, organization_id=appointment.organization_id)
    client_name = _client_display_name(appointment.external_client_id)
    if appointment.status == 'cancelled':
        title = 'Appointment cancelled'
        body = f'The appointment with {client_name} has been cancelled.'
    elif 'staff_id' in changed_fields:
        title = 'Appointment assignment changed'
        body = f'The staff assignment for your appointment with {client_name} has changed.'
    else:
        title = 'Appointment schedule changed'
        body = f'The time or details for your appointment with {client_name} have changed.'
    for recipient in recipients:
        _create(
            recipient_id=recipient.id,
            event_type='appointment_changed',
            title=title,
            body=body,
            data={
                'appointment_id': appointment.id,
                'client_id': appointment.external_client_id,
                'url': '/reporting/appointments',
            },
        )


def notify_session_attachment_added(media):
    """Notify staff assigned to this client that a file/photo/video was attached to a session."""
    session_run = media.session_run
    client = _resolve_local_client(session_run.external_client_id)
    recipients = _assigned_staff_for(client, exclude_user_id=media.created_by_id)
    client_name = _client_display_name(session_run.external_client_id)
    for recipient in recipients:
        _create(
            recipient_id=recipient.id,
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
    """Notify staff assigned to this client that a file/photo/video failed to upload."""
    client = _resolve_local_client(session_run.external_client_id)
    recipients = _assigned_staff_for(client, exclude_user_id=getattr(uploader, 'id', None))
    client_name = _client_display_name(session_run.external_client_id)
    uploader_name = f'{uploader.first_name} {uploader.last_name}'.strip() if uploader else 'A staff member'
    for recipient in recipients:
        _create(
            recipient_id=recipient.id,
            event_type='file_upload_failed',
            title='File upload failed',
            body=f'{uploader_name} tried to upload a {media_type} for {client_name} but it failed.',
            data={'session_id': session_run.id, 'client_id': session_run.external_client_id},
        )


def notify_target_advanced(target, session_run):
    """Notify staff assigned to this client that a target auto-advanced."""
    client = _resolve_local_client(session_run.external_client_id)
    recipients = _assigned_staff_for(client)
    for recipient in recipients:
        _create(
            recipient_id=recipient.id,
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
    """Notify staff assigned to this client that a target reached mastery."""
    client = _resolve_local_client(session_run.external_client_id)
    recipients = _assigned_staff_for(client)
    for recipient in recipients:
        _create(
            recipient_id=recipient.id,
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
    """Notify staff assigned to this client that a target moved back to
    active status after being mastered/closed/discontinued/on hold."""
    client = _resolve_local_client(target.program.external_client_id)
    recipients = _assigned_staff_for(client, exclude_user_id=getattr(reviewer, 'id', None))
    for recipient in recipients:
        _create(
            recipient_id=recipient.id,
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
    """Notify staff assigned to this client that a program was modified.
    Org-level template programs have no client — fall back to admins/
    supervisors in that case, since there's no specific staff to target."""
    client = _resolve_local_client(program.external_client_id)
    if client is not None:
        recipients = _assigned_staff_for(client, exclude_user_id=getattr(editor, 'id', None))
    else:
        recipients = _reviewers_for(program.organization_id, exclude_user_id=getattr(editor, 'id', None))
    for recipient in recipients:
        _create(
            recipient_id=recipient.id,
            event_type='program_modified',
            title=f'Program modified: {program.name}',
            body=f'"{program.name}" was updated.',
            data={'program_id': program.id, 'program_name': program.name},
        )


def notify_signature_request(note):
    """Notify the client's caregiver that a note is awaiting their signature."""
    from apps.accounts.models import User
    caregivers = User.objects.filter(
        role='caregiver',
        external_client_id=note.external_client_id,
        organization_id=note.organization_id,
    )
    for caregiver in caregivers:
        _create(
            recipient_id=caregiver.id,
            event_type='signature_request',
            title='Signature requested',
            body='A session note is ready for your signature.',
            data={'note_id': note.id, 'client_id': note.external_client_id},
        )


def notify_target_prompt_level_changed(target, session_run, direction: str, new_level_label: str):
    """Notify staff assigned to this client that a target's prompt level auto-faded.

    direction: 'advanced' or 'regressed'.
    """
    client = _resolve_local_client(session_run.external_client_id)
    recipients = _assigned_staff_for(client)
    for recipient in recipients:
        _create(
            recipient_id=recipient.id,
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
