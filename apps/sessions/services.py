from datetime import timedelta

from django.utils import timezone
from ninja.errors import HttpError

from apps.programs.models import Program, Lesson


def _workflow_phase_for_target(target, phase: str) -> dict | None:
    wf = target.workflow_template or target.program.workflow_template
    if not wf:
        return None
    return next((p for p in wf.phases if p.get('phase') == phase), None)


def _maintenance_interval_days(row: dict) -> int:
    try:
        value = max(1, int(row.get('value') or 1))
    except (TypeError, ValueError):
        value = 1
    interval_type = row.get('type') or 'daily'
    if interval_type == 'weekly':
        return value * 7
    if interval_type == 'biweekly':
        return value * 14
    if interval_type == 'monthly':
        return value * 30
    return value


def _target_due_for_session(target) -> bool:
    """Keep mastered maintenance targets out of session snapshots until due.

    A target that has not yet completed a successful maintenance episode, or
    failed the most recent maintenance check, remains due every day because
    `maintenance_episodes_completed` is reset to 0 by the evaluator.
    """
    if target.status != 'mastered':
        return True

    phase = _workflow_phase_for_target(target, 'mastered')
    maintenance = (phase or {}).get('maintenance') or {}
    intervals = maintenance.get('intervals') or []
    if not intervals:
        return True
    if target.maintenance_episodes_completed <= 0:
        return True

    from apps.sessions.models import BehaviorEvent, TrialEvent
    from apps.programs.models import TargetStatusChange

    entered = (
        TargetStatusChange.objects
        .filter(target=target, to_status='mastered')
        .order_by('-created_at')
        .first()
    )
    data_since = {'session_run__submitted_at__isnull': False}
    if entered:
        data_since['session_run__submitted_at__gte'] = entered.created_at

    last_trial = (
        TrialEvent.objects
        .filter(target_id=target.id, **data_since)
        .order_by('-session_run__submitted_at')
        .values_list('session_run__submitted_at', flat=True)
        .first()
    )
    last_behavior = (
        BehaviorEvent.objects
        .filter(target_id=target.id, **data_since)
        .order_by('-session_run__submitted_at')
        .values_list('session_run__submitted_at', flat=True)
        .first()
    )
    last_seen = max([dt for dt in (last_trial, last_behavior) if dt], default=None)
    if not last_seen:
        return True

    interval_index = min(target.maintenance_episodes_completed - 1, len(intervals) - 1)
    due_at = last_seen + timedelta(days=_maintenance_interval_days(intervals[interval_index]))
    return timezone.now() >= due_at


def build_program_snapshot(client_id: int, lesson_id: int | None = None, restrict_to_lesson: bool = False) -> dict:
    """
    Captures the full program/target configuration as an immutable JSONB snapshot.
    Called once when a SessionRun is created.

    The snapshot includes:
    - Programs (filtered to active, scoped to lesson if provided)
    - Targets (only those visible to staff — Teaching/Baseline/Acquisition/Maintenance)
    - Full prompting template levels for each target

    This means changes a supervisor makes after a session starts never affect
    historical reporting — the snapshot is the source of truth for that session.
    """
    snapshot: dict = {
        'captured_at': timezone.now().isoformat(),
        'lesson_id': lesson_id,
        'lesson_name': None,
        'programs': [],
    }

    if lesson_id:
        try:
            lesson = Lesson.objects.get(id=lesson_id, is_active=True)
        except Lesson.DoesNotExist:
            raise HttpError(404, 'Lesson not found or not active')
        snapshot['lesson_name'] = lesson.name
        program_ids = lesson.lesson_programs.values_list('program_id', flat=True)
        programs_qs = (
            Program.objects
            .filter(id__in=program_ids, status=Program.Status.ACTIVE)
            .prefetch_related('targets__prompting_template')
        )
    elif not restrict_to_lesson:
        programs_qs = (
            Program.objects
            .filter(external_client_id=client_id, status=Program.Status.ACTIVE)
            .prefetch_related('targets__prompting_template')
        )
    else:
        programs_qs = Program.objects.none()

    for program in programs_qs:
        targets_data = []
        for target in program.targets.visible_to_staff():
            if not _target_due_for_session(target):
                continue
            pt = target.prompting_template
            targets_data.append({
                'id': target.id,
                'name': target.name,
                'status': target.status,
                'measurement_type': target.measurement_type,
                'sub_items': target.sub_items,
                'sd_text': target.sd_text,
                'teaching_instructions': target.teaching_instructions,
                'prompting_template': {
                    'id': pt.id,
                    'name': pt.name,
                    'levels': pt.levels,
                } if pt else None,
                'current_prompt_level_index': target.current_prompt_level_index,
                'fading_mode': target.fading_mode,
            })

        snapshot['programs'].append({
            'id': program.id,
            'name': program.name,
            'category': program.category,
            'treatment_area': program.treatment_area,
            'targets': targets_data,
        })

    return snapshot


def _assert_editable(session_run) -> None:
    """Raise 409 if the session is no longer in the open state."""
    if not session_run.is_editable:
        raise HttpError(409, f'Session is {session_run.status} and cannot be modified')


def submit_session(session_run, staff_user) -> tuple[list, list]:
    """Move a session from open → submitted, then evaluate target workflow
    advancement and prompt-level fading.

    A supervisor or admin submitting a session already carries approval
    authority, so their submission skips the review queue and lands directly
    on approved — a plain staff submission still requires a separate
    supervisor/admin approval.

    Returns (advanced_targets, faded_targets) — the Target objects whose
    status was automatically advanced, and whose prompt level was automatically
    faded, respectively.
    """
    _assert_editable(session_run)
    if session_run.staff_id != staff_user.id and staff_user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Only the session owner or a supervisor can submit')

    # Only sessions where a DocuSeal Session Note template was actually
    # selected are gated — sessions that never started one submit as before.
    note = getattr(session_run, 'note', None)
    if note and note.docuseal_template_id and not note.docuseal_completed_at:
        raise HttpError(409, 'Session Note must be completed before submitting')

    auto_approve = staff_user.role in ('admin', 'supervisor')
    now = timezone.now()
    session_run.submitted_at = now
    session_run.ended_at = session_run.ended_at or now
    update_fields = ['submitted_at', 'ended_at', 'status']
    if auto_approve:
        session_run.status = session_run.Status.APPROVED
        session_run.reviewed_by = staff_user
        session_run.reviewed_at = now
        update_fields += ['reviewed_by', 'reviewed_at']
    else:
        session_run.status = session_run.Status.SUBMITTED
    session_run.save(update_fields=update_fields)

    from apps.notifications.service import notify_session_submitted, notify_session_approved
    if auto_approve:
        notify_session_approved(session_run)
    else:
        notify_session_submitted(session_run)

    from apps.programs.services import evaluate_session_mastery, evaluate_session_fading
    advanced = evaluate_session_mastery(session_run)
    faded = evaluate_session_fading(session_run)
    return advanced, faded


def approve_session(session_run, reviewer) -> None:
    """Move a session from submitted → approved."""
    if session_run.status != session_run.Status.SUBMITTED:
        raise HttpError(409, f'Session must be submitted before approval (current: {session_run.status})')
    session_run.status = session_run.Status.APPROVED
    session_run.reviewed_by = reviewer
    session_run.reviewed_at = timezone.now()
    session_run.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    from apps.notifications.service import notify_session_approved
    notify_session_approved(session_run)


def reject_session(session_run, reviewer, reason: str) -> None:
    """Move a session from submitted → rejected."""
    if session_run.status != session_run.Status.SUBMITTED:
        raise HttpError(409, f'Session must be submitted before rejection (current: {session_run.status})')
    if not reason.strip():
        raise HttpError(400, 'A rejection reason is required')
    session_run.status = session_run.Status.REJECTED
    session_run.reviewed_by = reviewer
    session_run.reviewed_at = timezone.now()
    session_run.rejection_reason = reason
    session_run.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'rejection_reason'])

    from apps.notifications.service import notify_session_rejected
    notify_session_rejected(session_run)
