from __future__ import annotations

from apps.programs.models import Target, TargetPromptLevelChange, TargetStatusChange


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


def evaluate_session_fading(session_run) -> list[Target]:
    """
    Called immediately after a SessionRun is submitted (same call site as
    evaluate_session_mastery). For every target that had trial events in this
    session, checks whether the target's fading_mode is automatic, and if so
    whether recent performance at its *current* prompt level meets the
    fading_template's rules — advancing (less intrusive) or regressing (more
    intrusive) the target's current_prompt_level_index accordingly.

    Returns the list of targets whose prompt level was changed.
    """
    from apps.sessions.models import TrialEvent

    target_ids = (
        TrialEvent.objects
        .filter(session_run=session_run)
        .values_list('target_id', flat=True)
        .distinct()
    )

    faded: list[Target] = []
    for target in Target.objects.filter(id__in=target_ids).select_related(
        'prompting_template', 'fading_template', 'program__fading_template',
    ):
        if target.fading_mode != 'automatic':
            continue
        if _fade_if_criteria_met(target, session_run.id):
            faded.append(target)

    return faded


def _pass_stats(target: Target, trials_qs) -> tuple[int, int]:
    """
    Returns (total_passes, correct_passes) for one target's trials in one session.

    - Plain targets (no sub_items — discrete_trial and friends): one TrialEvent row
      is one pass; correct means response_score > 0. Unchanged from before sub_items existed.
    - Shaping: one row per pass (whichever sub_item_key/level was reached that trial);
      correct only if the level reached is the terminal (last) entry in target.sub_items.
    - Task analysis / set of targets: multiple rows share one trial_number, together
      forming one pass. A pass only counts once every sub_item has been scored in it
      (an in-progress/incomplete pass doesn't count toward total or correct), and is
      correct only if every one of those rows was scored correct — independent
      completion of the whole chain/set, not a per-step average.
    """
    if not target.sub_items:
        total = trials_qs.count()
        correct = trials_qs.filter(response_score__gt=0).count()
        return total, correct

    if target.measurement_type == Target.MeasurementType.SHAPING:
        terminal_key = target.sub_items[-1].get('key')
        total = trials_qs.count()
        correct = trials_qs.filter(sub_item_key=terminal_key).count()
        return total, correct

    expected_keys = {item.get('key') for item in target.sub_items}
    scored_keys: dict[int, set] = {}
    correct_keys: dict[int, set] = {}
    for score, trial_number, key in trials_qs.values_list('response_score', 'trial_number', 'sub_item_key'):
        scored_keys.setdefault(trial_number, set()).add(key)
        if score > 0:
            correct_keys.setdefault(trial_number, set()).add(key)

    total = 0
    correct = 0
    for trial_number, keys in scored_keys.items():
        if keys != expected_keys:
            continue
        total += 1
        if correct_keys.get(trial_number) == expected_keys:
            correct += 1
    return total, correct


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
    # 'min' (Increase Percent Correct): percent must be ≥ threshold
    # 'max' (Reduce Percentage): percent must be ≤ threshold
    threshold_direction = str(criteria.get('threshold_direction') or 'min').lower()

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
        total, correct = _pass_stats(target, trials)
        if total < min_trials:
            return False
        pct = correct / total * 100
        if threshold_direction == 'max':
            if pct > threshold_pct:
                return False
        elif pct < threshold_pct:
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


def _fade_if_criteria_met(target: Target, session_run_id: int) -> bool:
    """
    Returns True if the target's current_prompt_level_index was changed.

    Resolves the target's FadingTemplate (target override or program default),
    looks at the last `consecutive_sessions` submitted/approved sessions'
    trials recorded at the target's *current* prompt level, and advances
    (moves to the next less-intrusive level) if all of them meet threshold_pct,
    or regresses (moves to the next more-intrusive level) if all of them are
    at/below regression_threshold_pct. Mixed/plateaued performance is a no-op.
    """
    ft = target.fading_template or target.program.fading_template
    if not ft:
        return False

    levels = target.prompting_template.levels if target.prompting_template else []
    if len(levels) < 2:
        return False

    idx = min(target.current_prompt_level_index, len(levels) - 1)
    if idx != target.current_prompt_level_index:
        # Stale index (e.g. prompting_template's levels were edited down) —
        # correct silently, no audit row: this is a data-integrity fix, not a
        # fading decision.
        Target.objects.filter(id=target.id).update(current_prompt_level_index=idx)

    current_label = levels[idx].get('label')

    rules = ft.rules
    n_consecutive = rules.get('consecutive_sessions', 3)
    threshold_pct = rules.get('threshold_pct', 90)
    min_trials = rules.get('minimum_trials', 5)
    regression_threshold_pct = rules.get('regression_threshold_pct', 50)

    from apps.sessions.models import SessionRun, TrialEvent

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

    all_advance = True
    all_regress = True
    for session in recent_sessions:
        trials = TrialEvent.objects.filter(
            session_run=session, target_id=target.id, prompt_level_label=current_label,
        )
        total, correct = _pass_stats(target, trials)
        if total < min_trials:
            # Insufficient data at this level for this session — blocks the
            # whole evaluation this run, same conservative behavior as mastery.
            return False
        pct = correct / total * 100
        if pct < threshold_pct:
            all_advance = False
        if pct > regression_threshold_pct:
            all_regress = False

    if all_advance:
        new_idx = idx + 1
        if new_idx >= len(levels):
            return False  # already at the least-intrusive level
    elif all_regress:
        new_idx = idx - 1
        if new_idx < 0:
            return False  # already at the most-intrusive level
    else:
        return False

    target.current_prompt_level_index = new_idx
    target.save(update_fields=['current_prompt_level_index', 'updated_at'])

    new_label = levels[new_idx].get('label')
    target._pre_fade_from_label = current_label
    target._pre_fade_to_label = new_label
    TargetPromptLevelChange.objects.create(
        target=target,
        from_level_index=idx,
        to_level_index=new_idx,
        from_level_label=current_label,
        to_level_label=new_label,
        trigger=TargetPromptLevelChange.Trigger.AUTO_FADING,
        session_run_id=session_run_id,
    )

    from apps.notifications.service import notify_target_prompt_level_changed
    from apps.sessions.models import SessionRun as _SessionRun
    try:
        sr = _SessionRun.objects.get(id=session_run_id)
        direction = 'advanced' if all_advance else 'regressed'
        notify_target_prompt_level_changed(target, sr, direction, new_label)
    except Exception:
        pass

    return True
