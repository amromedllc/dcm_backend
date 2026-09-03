from __future__ import annotations

from django.db.models import Min, Max, Sum

from apps.programs.models import Target, TargetPromptLevelChange, TargetStatusChange, TargetSubItem, TargetSubItemStatusChange
from apps.programs.measurements import (
    aggregate_measurement,
    objective_key_for_measurement,
    DURATION_MEASUREMENTS,
    RATE_MEASUREMENTS,
)


# Target types whose data is recorded as BehaviorEvents rather than trials.
# 'frequency' is a legacy MeasurementType value (no longer offered when
# creating a target) that some existing rows still carry — kept as a plain
# string so this set survives the enum no longer defining it.
BEHAVIOR_MEASUREMENT_TYPES = {
    Target.MeasurementType.DURATION,
    Target.MeasurementType.RATE,
    'frequency',
}


def _scorable_sub_items(sub_items: list[dict]) -> list[dict]:
    scorable = [
        item for item in sub_items
        if not item.get('status') or item.get('status') in {'probe', 'acquisition'}
    ]
    return scorable or sub_items


def _sync_target_sub_items_json(target: Target) -> None:
    items = [
        {'key': item.key, 'label': item.label, 'status': item.status}
        for item in target.child_items.all().order_by('display_order', 'id')
    ]
    if items and target.sub_items != items:
        target.sub_items = items
        target.save(update_fields=['sub_items', 'updated_at'])


def _transition_sub_item(sub_item: TargetSubItem, next_status: str, session_run_id: int) -> bool:
    if not next_status or sub_item.status == next_status:
        return False
    old_status = sub_item.status
    sub_item.status = next_status
    sub_item.save(update_fields=['status', 'updated_at'])
    TargetSubItemStatusChange.objects.create(
        sub_item=sub_item,
        from_status=old_status,
        to_status=next_status,
        trigger=TargetSubItemStatusChange.Trigger.AUTO_MASTERY,
        session_run_id=session_run_id,
    )
    return True


def _advance_sub_items_if_needed(target: Target, session_run_id: int) -> bool:
    if target.measurement_type not in {
        Target.MeasurementType.TASK_ANALYSIS,
        Target.MeasurementType.SET_OF_TARGETS,
        Target.MeasurementType.SHAPING,
    }:
        return False

    children = list(target.child_items.all().order_by('display_order', 'id'))
    if not children:
        return False

    open_statuses = {'probe', 'acquisition'}
    waiting = [item for item in children if item.status == TargetSubItem.Status.WAITING]
    current = [item for item in children if item.status in open_statuses]

    if target.sub_item_progression == Target.SubItemProgression.TOTAL_TASK:
        changed = False
        for item in current:
            changed = _transition_sub_item(item, TargetSubItem.Status.MASTERED, session_run_id) or changed
        if waiting:
            for item in waiting:
                changed = _transition_sub_item(item, TargetSubItem.Status.ACQUISITION, session_run_id) or changed
            _sync_target_sub_items_json(target)
            return True
        _sync_target_sub_items_json(target)
        return changed and any(item.status != TargetSubItem.Status.MASTERED for item in children)

    changed = False
    for item in current:
        changed = _transition_sub_item(item, TargetSubItem.Status.MASTERED, session_run_id) or changed

    if waiting:
        next_item = waiting[-1] if target.sub_item_progression == Target.SubItemProgression.BACKWARD else waiting[0]
        _transition_sub_item(next_item, TargetSubItem.Status.ACQUISITION, session_run_id)
        _sync_target_sub_items_json(target)
        return True

    _sync_target_sub_items_json(target)
    return False


def evaluate_session_mastery(session_run) -> list[Target]:
    """
    Called immediately after a SessionRun is approved.

    For every target that had trial events in this session, checks whether the
    target's current workflow phase criteria are now satisfied across the required
    number of consecutive approved sessions. Advances any target that qualifies.

    Returns the list of targets whose status was changed.
    """
    from apps.sessions.models import BehaviorEvent, TrialEvent

    trial_target_ids = (
        TrialEvent.objects
        .filter(session_run=session_run)
        .values_list('target_id', flat=True)
        .distinct()
    )
    behavior_target_ids = (
        BehaviorEvent.objects
        .filter(session_run=session_run)
        .values_list('target_id', flat=True)
        .distinct()
    )
    target_ids = set(trial_target_ids) | set(behavior_target_ids)

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


def _pass_stats(target: Target, trials_qs, *, require_independent: bool = False) -> tuple[int, int]:
    """
    Returns (total_passes, correct_passes) for one target's trials in one session.

    - Plain targets (no sub_items — discrete_trial and friends): one TrialEvent row
      is one pass; correct means response_score > 0 by default. Unchanged from
      before sub_items existed.
    - Shaping: one row per pass (whichever sub_item_key/level was reached that trial);
      correct only if the level reached is the terminal (last) entry in target.sub_items.
      `require_independent` doesn't apply here — correctness is already defined by
      reaching the terminal level, not by response_score.
    - Task analysis / set of targets: multiple rows share one trial_number, together
      forming one pass. A pass only counts once every sub_item has been scored in it
      (an in-progress/incomplete pass doesn't count toward total or correct), and is
      correct only if every one of those rows was scored correct — independent
      completion of the whole chain/set, not a per-step average.

    `require_independent`: when True, a pass only counts as correct if its score
    equals the target's configured *success* score — the level an admin marked
    is_success on the prompting template (falling back to the highest configured
    score for templates with nothing marked) — the same rule apps.analytics uses
    for accuracy charts. Used by mastery (_advance_if_criteria_met), since
    advancing to the next phase should require independent performance, not
    merely a nonzero (still-prompted) score. NOT used by fading
    (_fade_if_criteria_met): fading evaluates trials already filtered down to
    the target's *current* (often sub-maximal) prompt level, so requiring the
    success score there would make it impossible to ever satisfy — fading keeps
    the plain response_score > 0 rule.
    """
    max_score = None
    if require_independent and target.prompting_template:
        max_score = target.prompting_template.success_score()

    if not target.sub_items:
        total = trials_qs.count()
        correct = (
            trials_qs.filter(response_score__gte=max_score).count() if max_score is not None
            else trials_qs.filter(response_score__gt=0).count()
        )
        return total, correct

    if target.measurement_type == Target.MeasurementType.SHAPING:
        terminal_key = target.sub_items[-1].get('key')
        total = trials_qs.count()
        correct = trials_qs.filter(sub_item_key=terminal_key).count()
        return total, correct

    expected_keys = {item.get('key') for item in _scorable_sub_items(target.sub_items)}
    # A Duration step has no prompt hierarchy — it "passes" a chain iteration
    # whenever an observation (value_seconds) was recorded for it, regardless
    # of the parent's success score.
    duration_keys = {
        item.get('key') for item in target.sub_items
        if item.get('measurement_type') == 'duration'
    }
    scored_keys: dict[int, set] = {}
    correct_keys: dict[int, set] = {}
    for score, trial_number, key, value_seconds in trials_qs.values_list(
        'response_score', 'trial_number', 'sub_item_key', 'value_seconds',
    ):
        scored_keys.setdefault(trial_number, set()).add(key)
        if key in duration_keys:
            is_correct = value_seconds is not None
        else:
            is_correct = score >= max_score if max_score is not None else score > 0
        if is_correct:
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


def _limit_trials_if_configured(trials_qs, criteria: dict):
    only_first = criteria.get('only_probe_first') or criteria.get('only_first_trials') or criteria.get('first_trials')
    try:
        limit = int(only_first or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0:
        return trials_qs
    trial_numbers = list(
        trials_qs
        .values_list('trial_number', flat=True)
        .distinct()
        .order_by('trial_number')[:limit]
    )
    return trials_qs.filter(trial_number__in=trial_numbers)


def _observation_seconds(session, events) -> float | None:
    """Length of the window a rate is measured over: the session's own
    start→end if known, else the span between the first and last recorded
    event. None when neither is usable."""
    start = session.started_at
    end = session.ended_at or session.submitted_at or session.reviewed_at
    if not start or not end or end <= start:
        bounds = events.aggregate(start=Min('occurred_at'), end=Max('occurred_at'))
        start, end = bounds['start'], bounds['end']
    if not start or not end or end <= start:
        return None
    return (end - start).total_seconds()


def _behavior_metric_value(target: Target, session, criteria: dict) -> tuple[int, float]:
    """Returns (sample_count, value) for behavior-style workflow objectives.

    `minimum_trials` maps to minimum behavior events for behavior targets, keeping
    the existing workflow JSON shape without adding another field.
    """
    from apps.sessions.models import BehaviorEvent

    events = BehaviorEvent.objects.filter(session_run=session, target_id=target.id)
    total_events = events.aggregate(total=Sum('frequency_count'))['total'] or 0
    objective_key = str(criteria.get('objective_key') or '').lower()

    # No explicit workflow objective_key — the target's own `measurement`
    # fully decides which metric to roll up to (min/max/avg/total duration,
    # rate per hour/minute, or raw frequency).
    if not objective_key and target.measurement:
        m = target.measurement
        if m in DURATION_MEASUREMENTS:
            durations = [d for d in events.values_list('duration_seconds', flat=True) if d is not None]
            return len(durations), aggregate_measurement(m, durations=durations)
        if m in RATE_MEASUREMENTS:
            return total_events, aggregate_measurement(
                m, event_count=total_events, seconds_elapsed=_observation_seconds(session, events),
            )
        return total_events, float(total_events)  # frequency / percent_correct

    if target.measurement_type == Target.MeasurementType.DURATION or 'duration' in objective_key:
        durations = [d for d in events.values_list('duration_seconds', flat=True) if d is not None]
        if not durations:
            return 0, 0.0
        if 'min_duration' in objective_key:
            return len(durations), float(min(durations))
        if 'max_duration' in objective_key:
            return len(durations), float(max(durations))
        if 'avg_duration' in objective_key:
            return len(durations), float(sum(durations) / len(durations))
        return len(durations), float(sum(durations))

    if target.measurement_type == Target.MeasurementType.RATE or 'rate_' in objective_key:
        start = session.started_at
        end = session.ended_at or session.submitted_at or session.reviewed_at
        if not start or not end or end <= start:
            bounds = events.aggregate(start=Min('occurred_at'), end=Max('occurred_at'))
            start = bounds['start']
            end = bounds['end']
        if not start or not end or end <= start:
            return total_events, float(total_events)
        hours = max((end - start).total_seconds() / 3600, 1 / 60)
        if 'per_minute' in objective_key:
            return total_events, float(total_events / (hours * 60))
        return total_events, float(total_events / hours)

    return total_events, float(total_events)


def _session_meets_criteria(target: Target, session, phase_config: dict, criteria: dict) -> bool:
    min_trials = criteria.get('minimum_trials', 5)
    threshold = criteria.get('threshold_pct', 80)
    threshold_direction = str(criteria.get('threshold_direction') or 'min').lower()

    # An explicit workflow objective_key wins; otherwise fall back to the one
    # implied by the target's own `measurement` (so a Duration target set to
    # "Min. Observed Duration" is evaluated on the minimum, not the total,
    # without every WorkflowTemplate having to spell it out).
    objective_key = str(
        criteria.get('objective_key')
        or phase_config.get('objective_key')
        or objective_key_for_measurement(target.measurement)
        or ''
    ).lower()
    criteria = {**criteria, 'objective_key': objective_key}

    if target.measurement_type in BEHAVIOR_MEASUREMENT_TYPES or objective_key in {
        'increase_frequency', 'reduce_frequency',
        'increase_rate_per_hour', 'reduce_rate_per_hour',
        'increase_rate_per_minute', 'reduce_rate_per_minute',
        'increase_total_duration', 'reduce_total_duration',
        'increase_min_duration', 'reduce_min_duration',
        'increase_max_duration', 'reduce_max_duration',
        'increase_avg_duration', 'reduce_avg_duration',
    }:
        total, value = _behavior_metric_value(target, session, criteria)
        if total < min_trials:
            return False
        return value <= threshold if threshold_direction == 'max' else value >= threshold

    from apps.sessions.models import TrialEvent

    trials = TrialEvent.objects.filter(session_run=session, target_id=target.id)
    trials = _limit_trials_if_configured(trials, criteria)
    total, correct = _pass_stats(target, trials, require_independent=True)
    if total < min_trials:
        return False
    pct = correct / total * 100
    return pct <= threshold if threshold_direction == 'max' else pct >= threshold


def _uses_behavior_events(target: Target, criteria: dict | None = None) -> bool:
    objective_key = str((criteria or {}).get('objective_key') or '').lower()
    return target.measurement_type in BEHAVIOR_MEASUREMENT_TYPES or objective_key in {
        'increase_frequency', 'reduce_frequency',
        'increase_rate_per_hour', 'reduce_rate_per_hour',
        'increase_rate_per_minute', 'reduce_rate_per_minute',
        'increase_total_duration', 'reduce_total_duration',
        'increase_min_duration', 'reduce_min_duration',
        'increase_max_duration', 'reduce_max_duration',
        'increase_avg_duration', 'reduce_avg_duration',
    }


def _recent_sessions_for_target(target: Target, limit: int, criteria: dict | None = None):
    from apps.sessions.models import SessionRun

    event_filter = (
        {'behavior_events__target_id': target.id}
        if _uses_behavior_events(target, criteria)
        else {'trial_events__target_id': target.id}
    )
    return list(
        SessionRun.objects
        .filter(
            status__in=[SessionRun.Status.SUBMITTED, SessionRun.Status.APPROVED],
            **event_filter,
        )
        .distinct()
        .order_by('-submitted_at')[:limit]
    )


def _transition_target(target: Target, next_status: str, session_run_id: int) -> bool:
    if not next_status or next_status == target.status:
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

    _maybe_create_phase_line(target, old_status, next_status, session_run_id)
    _maybe_auto_open_waiting_targets(target, session_run_id)

    from apps.notifications.service import notify_target_advanced, notify_target_mastered
    from apps.sessions.models import SessionRun
    try:
        sr = SessionRun.objects.get(id=session_run_id)
        notify_target_advanced(target, sr)
        if next_status == 'mastered':
            notify_target_mastered(target, sr)
    except Exception:
        pass

    return True


def _maybe_create_phase_line(target: Target, old_status: str, next_status: str, session_run_id: int) -> None:
    wf = target.workflow_template or target.program.workflow_template
    if not wf:
        return
    phase_config = next((p for p in wf.phases if p.get('phase') == next_status), None)
    if not phase_config or not phase_config.get('auto_phase_line'):
        return

    from apps.analytics.models import GraphAnnotation
    from apps.sessions.models import SessionRun

    try:
        session = SessionRun.objects.get(id=session_run_id)
        date = (session.submitted_at or session.ended_at or session.started_at).date()
        label = phase_config.get('phase_line_label') or phase_config.get('label') or next_status.title()
        GraphAnnotation.objects.get_or_create(
            program=target.program,
            target=target,
            annotation_type=GraphAnnotation.AnnotationType.PHASE_LINE,
            date=date,
            label=label,
            defaults={
                'color': phase_config.get('color') or '#666666',
                'style': phase_config.get('phase_line_style') or GraphAnnotation.LineStyle.SOLID,
                'notes': f'Automatic workflow transition: {old_status} to {next_status}',
            },
        )
    except Exception:
        pass


def _maybe_auto_open_waiting_targets(target: Target, session_run_id: int) -> None:
    wf = target.workflow_template or target.program.workflow_template
    if not wf:
        return
    phase_config = next((p for p in wf.phases if p.get('phase') == 'mastered'), None)
    if not phase_config or not phase_config.get('auto_open_waiting'):
        return
    if target.status not in {'mastered', 'closed', 'maintenance'}:
        return

    open_status = phase_config.get('auto_open_status') or 'acquisition'
    direction = phase_config.get('auto_open_direction') or 'first'
    try:
        max_open = int(phase_config.get('auto_open_max_targets') or 1)
    except (TypeError, ValueError):
        max_open = 1

    currently_open = target.program.targets.exclude(id=target.id).filter(status=open_status).count()
    slots = max(0, max_open - currently_open)
    if phase_config.get('auto_open_all'):
        slots = target.program.targets.filter(status='waiting').count()
    if slots <= 0:
        return

    qs = target.program.targets.filter(status='waiting')
    if direction == 'last':
        qs = qs.order_by('-display_order', '-id')
    else:
        qs = qs.order_by('display_order', 'id')

    for waiting in qs[:slots]:
        _transition_target(waiting, open_status, session_run_id)


def _evaluate_maintenance(target: Target, session_run_id: int, phase_config: dict) -> bool:
    maintenance = phase_config.get('maintenance') or {}
    criteria = {
        'threshold_pct': maintenance.get('threshold_pct', 100),
        'threshold_direction': maintenance.get('threshold_direction', 'min'),
        'minimum_trials': maintenance.get('minimum_trials', 1),
        'objective_key': phase_config.get('objective_key'),
    }

    from apps.sessions.models import SessionRun

    try:
        session = SessionRun.objects.get(id=session_run_id)
    except SessionRun.DoesNotExist:
        return False

    if _session_meets_criteria(target, session, phase_config, criteria):
        target.maintenance_episodes_completed += 1
        intervals = maintenance.get('intervals') or []
        if target.maintenance_episodes_completed >= max(1, len(intervals)):
            target.maintenance_episodes_completed = 0
            target.save(update_fields=['maintenance_episodes_completed', 'updated_at'])
            next_status = phase_config.get('on_success') or 'closed'
            if next_status == 'maintenance':
                next_status = 'closed'
            return _transition_target(target, next_status, session_run_id)
        target.save(update_fields=['maintenance_episodes_completed', 'updated_at'])
        return False

    target.maintenance_episodes_completed = 0
    target.save(update_fields=['maintenance_episodes_completed', 'updated_at'])
    if maintenance.get('on_failure') != 'back_to_acquisition':
        return False

    revert_sessions = int(maintenance.get('revert_sessions') or 1)
    recent_sessions = _recent_sessions_for_target(target, revert_sessions, criteria)
    if len(recent_sessions) < revert_sessions:
        return False
    if all(not _session_meets_criteria(target, s, phase_config, criteria) for s in recent_sessions):
        return _transition_target(target, 'acquisition', session_run_id)
    return False


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
    if phase_config is None:
        return False

    if target.status == 'mastered' and phase_config.get('maintenance'):
        return _evaluate_maintenance(target, session_run_id, phase_config)

    if 'criteria' not in phase_config:
        return False

    next_status = phase_config.get('on_success')

    criteria = {**phase_config['criteria'], 'objective_key': phase_config.get('objective_key')}
    n_consecutive = criteria.get('consecutive_sessions', 3)

    # Most-recent-first so we look at the last N submitted/approved sessions for this target
    recent_sessions = _recent_sessions_for_target(target, n_consecutive, criteria)

    if len(recent_sessions) < n_consecutive:
        return False

    mastered = all(_session_meets_criteria(target, session, phase_config, criteria) for session in recent_sessions)
    if not mastered:
        failure_status = phase_config.get('on_failure_status') or phase_config.get('on_regression')
        return _transition_target(target, failure_status, session_run_id) if failure_status else False

    if _advance_sub_items_if_needed(target, session_run_id):
        return True

    return _transition_target(target, next_status, session_run_id)


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
