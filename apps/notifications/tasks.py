"""Scheduled notification tasks."""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.tenants.models import Organization
from shared.tenancy import tenant_context

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def send_upcoming_appointment_reminders() -> None:
    """Send one reminder for scheduled appointments starting within 30 minutes."""
    from apps.sessions.models import Appointment
    from apps.notifications.service import notify_appointment_reminder

    now = timezone.now()
    horizon = now + timedelta(minutes=30)
    for organization in Organization.objects.filter(is_active=True).iterator():
        with tenant_context(organization.id):
            appointments = Appointment.objects.filter(
                status=Appointment.Status.SCHEDULED,
                staff__isnull=False,
                reminder_sent_at__isnull=True,
                start_time__gt=now,
                start_time__lte=horizon,
            ).select_related('staff')
            for appointment in appointments.iterator():
                claimed = Appointment.objects.filter(
                    pk=appointment.pk,
                    reminder_sent_at__isnull=True,
                ).update(reminder_sent_at=now)
                if not claimed:
                    continue
                try:
                    notify_appointment_reminder(appointment)
                except Exception:
                    logger.exception(
                        'Could not send reminder for appointment=%s organization=%s',
                        appointment.id,
                        organization.id,
                    )
