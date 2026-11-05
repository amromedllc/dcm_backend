"""Organization-scoped appointment emails. No TPMS calls or clinical email content."""
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone
from apps.accounts.models import User
from apps.clients.models import Client
from apps.sessions.models import Appointment
from .models import AppointmentReminderSettings, AppointmentEmailDelivery, NotificationPreference, RoleNotificationPolicy
from .service import _practice_email_notifications_enabled

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


def reminder_settings():
    return AppointmentReminderSettings.objects.first() or AppointmentReminderSettings()


def resolve_appointment_client(appointment):
    if appointment.external_client_id is None:
        return None
    if appointment.source == Appointment.Source.SYNCED:
        if appointment.external_admin_id is None:
            return None
        clients = Client.objects.filter(external_id=str(appointment.external_client_id), external_admin_id=appointment.external_admin_id)
    else:
        clients = Client.objects.filter(pk=appointment.external_client_id)
        if appointment.external_admin_id is not None:
            clients = clients.filter(external_admin_id=appointment.external_admin_id)
    matches = list(clients[:2])
    return matches[0] if len(matches) == 1 else None


def email_allowed(recipient, event):
    """Match existing preference precedence, but fail closed on lookup errors."""
    try:
        if not _practice_email_notifications_enabled(recipient):
            return False
        pref = NotificationPreference.objects.filter(recipient=recipient, event_type=event).first()
        policy = RoleNotificationPolicy.objects.filter(role=recipient.role, event_type=event).first()
        if pref is not None and pref.locked is not None:
            return pref.email_enabled
        if policy is not None and policy.locked:
            return policy.email_enabled
        return pref.email_enabled if pref is not None else policy.email_enabled if policy is not None else True
    except Exception:
        logger.warning('Appointment email preferences unavailable for recipient id=%s', recipient.pk)
        return False


def recipients_for(appointment, config):
    client = resolve_appointment_client(appointment)
    if not config.email_enabled or client is None or client.status != Client.Status.ACTIVE or client.appointment_email_reminders_enabled is False:
        return []
    recipients = []
    if config.staff_enabled and appointment.staff_id:
        staff = User.objects.filter(Q(external_admin_id=client.external_admin_id) | Q(external_admin_id__isnull=True),
                                    pk=appointment.staff_id, organization_id=appointment.organization_id,
                                    is_active=True).exclude(role=User.Role.CAREGIVER).first()
        if staff is not None:
            recipients.append((staff, 'appointment_reminder'))
    if config.caregivers_enabled and client.external_id.isdigit() and client.external_admin_id is not None:
        recipients.extend((user, 'parent_session_reminder') for user in User.objects.filter(
            organization_id=appointment.organization_id, role=User.Role.CAREGIVER, is_active=True,
            external_admin_id=client.external_admin_id, external_client_id=int(client.external_id)))
    result = []
    for recipient, event in recipients:
        try:
            validate_email(recipient.email)
        except ValidationError:
            continue
        if email_allowed(recipient, event):
            result.append((recipient, event))
    return result


def _send(delivery, appointment, recipient):
    try:
        zone = ZoneInfo(recipient.timezone or settings.TIME_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo('UTC')
    start = appointment.start_time.astimezone(zone).strftime('%A, %B %d, %Y at %I:%M %p %Z')
    body = f'You have an upcoming appointment on {start}. Sign in to Progressly to view the details.'
    base = getattr(settings, 'FRONTEND_BASE_URL', '').rstrip('/')
    path = '/portal' if recipient.role == User.Role.CAREGIVER else '/reporting/appointments'
    html = render_to_string('notifications/email_notification.html', {
        'app_name': 'Progressly', 'title': 'Upcoming appointment', 'body': body,
        'cta_url': f'{base}{path}' if base else None,
        'preferences_url': f'{base}/account?section=notifications' if base and recipient.role != User.Role.CAREGIVER else None,
    })
    message = EmailMultiAlternatives('Upcoming appointment', body, settings.DEFAULT_FROM_EMAIL, [recipient.email],
                                    headers={'Message-ID': f'<appointment-reminder-{delivery.organization_id}-{delivery.pk}@progressly.io>'})
    message.attach_alternative(html, 'text/html')
    if message.send(fail_silently=False) != 1:
        raise RuntimeError('Email backend did not accept the reminder')


def deliver(appointment_id, recipient_id, scheduled_start):
    now = timezone.now()
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().filter(pk=appointment_id, status=Appointment.Status.SCHEDULED,
                                                                      start_time=scheduled_start, start_time__gt=now).first()
        if appointment is None:
            return
        # Recheck configuration, assignment and consent on every retry.
        config = reminder_settings()
        if appointment.start_time > now + timedelta(minutes=config.lead_minutes):
            return
        eligible = {user.pk: user for user, _event in recipients_for(appointment, config)}
        recipient = eligible.get(recipient_id)
        if recipient is None:
            return
        delivery, _ = AppointmentEmailDelivery.objects.get_or_create(appointment=appointment, recipient=recipient, scheduled_start=scheduled_start)
        if delivery.status == 'sent' or delivery.attempts >= MAX_ATTEMPTS or (delivery.retry_at and delivery.retry_at > now):
            return
        if delivery.status == 'sending' and delivery.claimed_at and delivery.claimed_at > now - timedelta(minutes=10):
            return
        delivery.status, delivery.claimed_at = 'sending', now
        delivery.attempts += 1
        delivery.save(update_fields=['status', 'claimed_at', 'attempts', 'updated_at'])
    try:
        _send(delivery, appointment, recipient)
    except Exception:
        # SMTP exceptions can contain addresses. Log only the internal delivery id.
        logger.warning('Appointment email delivery failed (delivery id=%s)', delivery.pk)
        AppointmentEmailDelivery.objects.filter(pk=delivery.pk, attempts=delivery.attempts, status='sending').update(status='failed', retry_at=now + timedelta(minutes=5 * delivery.attempts))
    else:
        AppointmentEmailDelivery.objects.filter(pk=delivery.pk, attempts=delivery.attempts, status='sending').update(status='sent', sent_at=timezone.now(), retry_at=None)


def send_organization_email_reminders():
    config = reminder_settings()
    if not config.email_enabled:
        return
    now = timezone.now()
    appointments = Appointment.objects.filter(status=Appointment.Status.SCHEDULED, start_time__gt=now,
                                               start_time__lte=now + timedelta(minutes=config.lead_minutes)).order_by('pk')
    for appointment in appointments.iterator():
        for recipient, _event in recipients_for(appointment, config):
            deliver(appointment.pk, recipient.pk, appointment.start_time)
