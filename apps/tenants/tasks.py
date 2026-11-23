import logging

from celery import shared_task
from django.utils import timezone

from .models import Organization

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def deactivate_expired_free_trials() -> None:
    """Deactivate organizations whose free trial period has ended.

    Runs against the public schema only — Organization is a TenantMixin model
    shared across all tenants, so no schema_context/tenant_context is needed.
    """
    expired = Organization.objects.filter(
        plan=Organization.Plan.FREE_TRIAL,
        is_active=True,
        trial_ends_at__isnull=False,
        trial_ends_at__lte=timezone.now(),
    )
    for org in expired.iterator():
        org.is_active = False
        org.save(update_fields=['is_active', 'updated_at'])
        logger.info('Deactivated organization %s (%s) — free trial expired', org.id, org.slug)
