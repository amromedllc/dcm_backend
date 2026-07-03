import logging
from django.utils import timezone

logger = logging.getLogger('dcm.audit')


def log_event(
    event_type: str,
    actor_id: int | None,
    resource_type: str,
    resource_id: int | str | None,
    metadata: dict | None = None,
) -> None:
    """
    Write an immutable audit event to the structured log.
    In production this feeds into a SIEM or audit database.
    Using structured logging here keeps the audit trail out of the
    main Django ORM so it cannot be accidentally modified by app code.
    """
    logger.info(
        'audit_event',
        extra={
            'event_type': event_type,
            'actor_id': actor_id,
            'resource_type': resource_type,
            'resource_id': str(resource_id) if resource_id is not None else None,
            'timestamp': timezone.now().isoformat(),
            'metadata': metadata or {},
        },
    )


# Convenience helpers used by other apps

def log_note_status_change(actor_id: int, note_id: int, old_status: str, new_status: str) -> None:
    log_event('note_status_change', actor_id, 'lesson_note', note_id,
              {'old_status': old_status, 'new_status': new_status})


def log_export(actor_id: int, export_id: int, export_type: str) -> None:
    log_event('export_generated', actor_id, 'export', export_id,
              {'export_type': export_type})


def log_api_key_use(key_id: int, endpoint: str) -> None:
    log_event('api_key_used', None, 'api_key', key_id,
              {'endpoint': endpoint})
