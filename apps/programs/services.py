from __future__ import annotations

from apps.programs.models import Target, TargetStatusChange


def evaluate_session_mastery(session_run) -> list[Target]:
    """
    Called immediately after a SessionRun is approved.

    For every target that had trial events in this session, checks whether the
    target's current workflow phase criteria are now satisfied across the required
    number of consecutive approved sessions. Advances any target that qualifies.

    Returns the list of targets whose status was changed.
    """
    from apps.sessions.models import TrialEvent

    target_ids = (
        TrialEvent.objects
        .filter(session_run=session_run)
        .values_list('target_id', flat=True)
        .distinct()
    )

    advanced: list[Target] = []
    for target in Target.objects.filter(id__in=target_ids).select_related('workflow_template', 'program__workflow_template'):
        if target.mastery_mode != 'automatic':
            continue
        if _advance_if_criteria_met(target, session_run.id):
            advanced.append(target)

    return advanced


def _advance_if_criteria_met(target: Target, session_run_id: int) -> bool:
    """
    Returns True if the target's status was advanced.

    Looks up the WorkflowTemplate phase entry matching target.status, then checks
    whether the last `consecutive_sessions` approved sessions all met the
    threshold_pct and minimum_trials criteria. If so, transitions to on_success.
    """
    wf = target.workflow_template or target.program.workflow_template
    if not wf:
        return False
    phase_config = next(
        (p for p in wf.phases if p.get('phase') == target.status),
        None,
    )
    if phase_config is None or 'criteria' not in phase_config:
        return False

    next_status = phase_config.get('on_success')
    if not next_status or next_status == target.status:
        return False

    criteria = phase_config['criteria']
    n_consecutive = criteria.get('consecutive_sessions', 3)
    threshold_pct = criteria.get('threshold_pct', 80)
    min_trials = criteria.get('minimum_trials', 5)

    from apps.sessions.models import SessionRun, TrialEvent

    # Most-recent-first so we look at the last N submitted/approved sessions for this target
    recent_sessions = list(
        SessionRun.objects
        .filter(
            status__in=[SessionRun.Status.SUBMITTED, SessionRun.Status.APPROVED],
            trial_events__target_id=target.id,
        )
        .distinct()
        .order_by('-submitted_at')[:n_consecutive]
    )

    if len(recent_sessions) < n_consecutive:
        return False

    for session in recent_sessions:
        trials = TrialEvent.objects.filter(session_run=session, target_id=target.id)
        total = trials.count()
        if total < min_trials:
            return False
        correct = trials.filter(response_score=1).count()
        if (correct / total * 100) < threshold_pct:
            return False

    old_status = target.status
    target.status = next_status
    target._pre_advance_status = old_status
    target.save(update_fields=['status', 'updated_at'])

    TargetStatusChange.objects.create(
        target=target,
        from_status=old_status,
        to_status=next_status,
        trigger=TargetStatusChange.Trigger.AUTO_MASTERY,
        session_run_id=session_run_id,
    )

    from apps.notifications.service import notify_target_advanced
    from apps.sessions.models import SessionRun
    try:
        sr = SessionRun.objects.get(id=session_run_id)
        notify_target_advanced(target, sr)
    except Exception:
        pass

    return True
