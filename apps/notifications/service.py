"""
Thin helpers to create Notification rows. Called from sessions and programs services.
All failures are swallowed — notifications must never break the primary flow.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _create(recipient_id: int, event_type: str, title: str, body: str = '', data: dict | None = None):
    try:
        from .models import Notification
        Notification.objects.create(
            recipient_id=recipient_id,
            event_type=event_type,
            title=title,
            body=body,
            data=data or {},
        )
    except Exception:
        logger.exception('Failed to create notification (event=%s recipient=%s)', event_type, recipient_id)


def notify_session_submitted(session_run):
    """Notify all admins/supervisors that a session needs review."""
    from apps.accounts.models import User
    reviewers = User.objects.filter(role__in=['admin', 'supervisor'])
    client_id = session_run.tpms_client_id
    staff_name = f'{session_run.staff.first_name} {session_run.staff.last_name}'.strip() if session_run.staff else 'Staff'
    for reviewer in reviewers:
        _create(
            recipient_id=reviewer.id,
            event_type='session_submitted',
            title='Session submitted for review',
            body=f'{staff_name} submitted a session for client #{client_id}.',
            data={'session_id': session_run.id, 'client_id': client_id},
        )


def notify_session_approved(session_run):
    """Notify the staff member their session was approved."""
    if not session_run.staff_id:
        return
    _create(
        recipient_id=session_run.staff_id,
        event_type='session_approved',
        title='Session approved',
        body=f'Your session for client #{session_run.tpms_client_id} has been approved.',
        data={'session_id': session_run.id, 'client_id': session_run.tpms_client_id},
    )


def notify_session_rejected(session_run):
    """Notify the staff member their session was rejected."""
    if not session_run.staff_id:
        return
    _create(
        recipient_id=session_run.staff_id,
        event_type='session_rejected',
        title='Session rejected',
        body=f'Your session for client #{session_run.tpms_client_id} was rejected: {session_run.rejection_reason}',
        data={'session_id': session_run.id, 'client_id': session_run.tpms_client_id,
              'reason': session_run.rejection_reason},
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
