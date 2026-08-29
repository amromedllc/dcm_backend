from collections import defaultdict
from datetime import date, datetime
from typing import TypedDict

from apps.programs.models import Program, Target, ProgramModule, ProgramSubmodule, TargetSubItem
from apps.programs.measurements import (
    aggregate_measurement, MEASUREMENT_LABELS, MEASUREMENT_UNIT,
    DURATION_MEASUREMENTS, RATE_MEASUREMENTS,
)
from apps.sessions.models import TrialEvent, BehaviorEvent, SessionRun


# ---------------------------------------------------------------------------
# Type hints for aggregated data points
# ---------------------------------------------------------------------------

class TrialDataPoint(TypedDict):
    date: date
    target_id: int | str
    target_name: str
    module_id: int | None
    module_name: str | None
    submodule_id: int | None
    submodule_name: str | None
    total_trials: int
    correct_count: int
    pct_correct: float
    duration_seconds: float


class BehaviorDataPoint(TypedDict):
    date: date
    target_id: int
    target_name: str
    module_id: int | None
    module_name: str | None
    submodule_id: int | None
    submodule_name: str | None
    frequency: int
    total_duration_seconds: int
    # The target's configured `measurement` (blank for legacy rows) and the
    # single value that measurement rolls this day's events up to, plus a
    # ready-to-render label/unit. Charts should plot `measurement_value` when
    # `measurement` is set rather than picking frequency vs duration themselves.
    measurement: str
    measurement_value: float
    measurement_label: str
    measurement_unit: str


class TargetSummary(TypedDict):
    target_id: int
    target_name: str
    status: str
    phase: str | None
    module_id: int | None
    module_name: str | None
    submodule_id: int | None
    submodule_name: str | None
    total_trials: int
    total_sessions: int
    avg_pct_correct: float
    last_session_date: date | None
    trend: str      # improving | declining | stable | insufficient_data


class ProgramReport(TypedDict):
    program_id: int
    program_name: str
    category: str
    treatment_area: str
    status: str
    targets: list[TargetSummary]


class ClientProgressReport(TypedDict):
    client_id: int
    date_from: date
    date_to: date
    total_sessions: int
    approved_sessions: int
    submitted_sessions: int
    total_programs: int
    active_programs: int
    mastered_targets: int
    total_targets: int
    programs: list[ProgramReport]


class MasteryEvent(TypedDict):
    target_id: int
    target_name: str
    program_id: int
    program_name: str
    treatment_area: str
    program_status: str
    program_tags: list[str]
    mastered_at: datetime


class ProgramProgressStats(TypedDict):
    program_id: int
    program_name: str
    treatment_area: str
    status: str  # Program.status (active | inactive | archived) — not a target status
    tags: list[str]
    status_counts: dict[str, int]
    avg_trials_to_mastery: float | None
    avg_sessions_to_mastery: float | None


class ClientProgressOverview(TypedDict):
    client_id: int
    # All-time — mastery is a point-in-time fact, not a report-window metric.
    # date_from/date_to filtering (if wanted) happens client-side over
    # mastery_events, same as the chart-type/group-by/cumulative controls.
    mastery_events: list[MasteryEvent]
    programs: list[ProgramProgressStats]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _max_scores_for_targets(target_ids: list[int]) -> dict[int, int | None]:
    """Returns {target_id: success_score} for the given target IDs."""
    result: dict[int, int | None] = {}
    for target in Target.objects.filter(id__in=target_ids).select_related('prompting_template'):
        result[target.id] = target.prompting_template.success_score() if target.prompting_template else None
    return result


def _module_name_map(module_ids: set[int]) -> dict[int, str]:
    return {m.id: m.name for m in ProgramModule.objects.filter(id__in=module_ids)}


def _submodule_name_map(submodule_ids: set[int]) -> dict[int, str]:
    return {s.id: s.name for s in ProgramSubmodule.objects.filter(id__in=submodule_ids)}


def _sub_item_series(target_ids: list[int]) -> dict[tuple[int, str], dict]:
    result: dict[tuple[int, str], dict] = {}
    for item in TargetSubItem.objects.filter(target_id__in=target_ids).select_related('target'):
        result[(item.target_id, item.key)] = {
            'series_id': -item.id,
            'name': f'{item.target.name} > {item.label}',
            'status': item.status,
            'target': item.target,
        }
    return result


def _compute_trend(points: list[float]) -> str:
    """
    Compares the average accuracy of the first half of data points against the second half.
    Returns: improving | declining | stable | insufficient_data
    """
    if len(points) < 4:
        return 'insufficient_data'
    mid = len(points) // 2
    first_half_avg = sum(points[:mid]) / mid
    second_half_avg = sum(points[mid:]) / (len(points) - mid)
    delta = second_half_avg - first_half_avg
    if delta >= 5:
        return 'improving'
    if delta <= -5:
        return 'declining'
    return 'stable'


# ---------------------------------------------------------------------------
# Trial data — powers line graphs
# ---------------------------------------------------------------------------

def get_trial_data_by_day(
    target_ids: list[int],
    date_from: date,
    date_to: date,
    group_by: str = 'target',
) -> list[TrialDataPoint]:
    """
    Returns daily trial accuracy between two dates, one series per day+group.

    group_by='target' (default) — one series per target (or per sub-item for
    task-analysis-style targets, via _sub_item_series), suitable for
    rendering per-target accuracy lines on a program graph.

    group_by='prompt_level' — collapses all targets into one series per
    prompt_level_label, useful for automatic-prompt-fading programs.

    group_by='user' — collapses all targets into one series per the staff
    member who ran the session (SessionRun.staff), useful for comparing
    accuracy across RBTs.
    """
    if not target_ids:
        return []

    max_scores = _max_scores_for_targets(target_ids)

    base_fields = ['recorded_at__date', 'target_id', 'target_name', 'response_score', 'sub_item_key', 'session_run_id']
    qs = TrialEvent.objects.filter(
        target_id__in=target_ids,
        recorded_at__date__gte=date_from,
        recorded_at__date__lte=date_to,
    )
    if group_by == 'prompt_level':
        raw = list(qs.values(*base_fields, 'prompt_level_label'))
    elif group_by == 'user':
        raw = list(qs.values(*base_fields, 'session_run__staff_id', 'session_run__staff__first_name', 'session_run__staff__last_name'))
    else:
        raw = list(qs.values(*base_fields))

    # Rate/Learning-Opps-rate metrics divide by session duration — each
    # session's (ended_at - started_at) counted once, not once per trial,
    # since one session can hold many trials across many targets. Sessions
    # still open (no ended_at) contribute 0 rather than skewing the rate.
    session_ids = {e['session_run_id'] for e in raw}
    session_seconds: dict[int, float] = {
        s.id: (s.ended_at - s.started_at).total_seconds()
        for s in SessionRun.objects.filter(id__in=session_ids, ended_at__isnull=False).only('id', 'started_at', 'ended_at')
    }

    # Build module/submodule lookup from live Target rows — only meaningful
    # for the per-target grouping; other groupings collapse across targets.
    target_meta: dict[int, dict] = {
        t.id: {'module_id': t.module_id, 'submodule_id': t.submodule_id}
        for t in Target.objects.filter(id__in=target_ids).only('id', 'module_id', 'submodule_id')
    }
    mod_ids = {m['module_id'] for m in target_meta.values() if m['module_id']}
    sub_ids = {m['submodule_id'] for m in target_meta.values() if m['submodule_id']}
    mod_names = _module_name_map(mod_ids)
    sub_names = _submodule_name_map(sub_ids)
    child_series = _sub_item_series(target_ids) if group_by == 'target' else {}

    grouped: dict[tuple, dict] = defaultdict(lambda: {'total': 0, 'correct': 0, 'name': '', 'parent_target_id': None, 'session_ids': set()})
    for event in raw:
        if group_by == 'prompt_level':
            series_id: int | str = event['prompt_level_label'] or 'Unscored'
            series_name = series_id
        elif group_by == 'user':
            series_id = event['session_run__staff_id'] or 0
            full_name = f"{event.get('session_run__staff__first_name') or ''} {event.get('session_run__staff__last_name') or ''}".strip()
            series_name = full_name or 'Unknown'
        else:
            child = child_series.get((event['target_id'], event.get('sub_item_key') or ''))
            series_id = child['series_id'] if child else event['target_id']
            series_name = child['name'] if child else event['target_name']

        key = (event['recorded_at__date'], series_id)
        grouped[key]['total'] += 1
        grouped[key]['name'] = series_name
        grouped[key]['parent_target_id'] = event['target_id']
        grouped[key]['session_ids'].add(event['session_run_id'])
        max_score = max_scores.get(event['target_id'])
        is_correct = (
            event['response_score'] >= max_score if max_score is not None
            else event['response_score'] > 0
        )
        if is_correct:
            grouped[key]['correct'] += 1

    result: list[TrialDataPoint] = []
    for (day, sid), data in sorted(grouped.items()):
        total = data['total']
        correct = data['correct']
        meta = target_meta.get(data['parent_target_id'], {}) if group_by == 'target' else {}
        mid = meta.get('module_id')
        subid = meta.get('submodule_id')
        duration_seconds = sum(session_seconds.get(rid, 0.0) for rid in data['session_ids'])
        result.append({
            'date': day,
            'target_id': sid,
            'target_name': data['name'],
            'module_id': mid,
            'module_name': mod_names.get(mid) if mid else None,
            'submodule_id': subid,
            'submodule_name': sub_names.get(subid) if subid else None,
            'total_trials': total,
            'correct_count': correct,
            'pct_correct': round(correct / total * 100, 1) if total else 0.0,
            'duration_seconds': duration_seconds,
        })
    return result


# ---------------------------------------------------------------------------
# Behavior data — powers frequency/duration graphs
# ---------------------------------------------------------------------------

def get_behavior_data_by_day(
    target_ids: list[int],
    date_from: date,
    date_to: date,
) -> list[BehaviorDataPoint]:
    """Returns daily behavior frequency and duration per target."""
    if not target_ids:
        return []

    raw = list(
        BehaviorEvent.objects
        .filter(
            target_id__in=target_ids,
            occurred_at__date__gte=date_from,
            occurred_at__date__lte=date_to,
        )
        .values(
            'occurred_at__date', 'occurred_at', 'target_id', 'target_name',
            'frequency_count', 'duration_seconds',
        )
    )

    target_meta: dict[int, dict] = {
        t.id: {'module_id': t.module_id, 'submodule_id': t.submodule_id, 'measurement': t.measurement}
        for t in Target.objects.filter(id__in=target_ids).only(
            'id', 'module_id', 'submodule_id', 'measurement',
        )
    }
    mod_ids = {m['module_id'] for m in target_meta.values() if m['module_id']}
    sub_ids = {m['submodule_id'] for m in target_meta.values() if m['submodule_id']}
    mod_names = _module_name_map(mod_ids)
    sub_names = _submodule_name_map(sub_ids)

    grouped: dict[tuple, dict] = defaultdict(
        lambda: {'freq': 0, 'dur': 0, 'name': '', 'durations': [], 'first': None, 'last': None}
    )
    for event in raw:
        key = (event['occurred_at__date'], event['target_id'])
        g = grouped[key]
        g['freq'] += event['frequency_count']
        g['dur'] += event['duration_seconds'] or 0
        g['name'] = event['target_name']
        if event['duration_seconds'] is not None:
            g['durations'].append(event['duration_seconds'])
        ts = event['occurred_at']
        g['first'] = ts if g['first'] is None or ts < g['first'] else g['first']
        g['last'] = ts if g['last'] is None or ts > g['last'] else g['last']

    result: list[BehaviorDataPoint] = []
    for (day, tid), data in sorted(grouped.items()):
        meta = target_meta.get(tid, {})
        mid = meta.get('module_id')
        sid = meta.get('submodule_id')
        measurement = meta.get('measurement') or ''

        if measurement in DURATION_MEASUREMENTS:
            value = aggregate_measurement(measurement, durations=data['durations'])
        elif measurement in RATE_MEASUREMENTS:
            span = (
                (data['last'] - data['first']).total_seconds()
                if data['first'] and data['last'] and data['last'] > data['first']
                else None
            )
            value = aggregate_measurement(measurement, event_count=data['freq'], seconds_elapsed=span)
        else:
            value = float(data['freq'])  # frequency / percent_correct / legacy

        result.append({
            'date': day,
            'target_id': tid,
            'target_name': data['name'],
            'module_id': mid,
            'module_name': mod_names.get(mid) if mid else None,
            'submodule_id': sid,
            'submodule_name': sub_names.get(sid) if sid else None,
            'frequency': data['freq'],
            'total_duration_seconds': data['dur'],
            'measurement': measurement,
            'measurement_value': round(value, 2),
            'measurement_label': MEASUREMENT_LABELS.get(measurement, 'Frequency'),
            'measurement_unit': MEASUREMENT_UNIT.get(measurement, 'count'),
        })
    return result


# ---------------------------------------------------------------------------
# Program summary — powers the target card grid on the program detail page
# ---------------------------------------------------------------------------

def get_program_summary(program_id: int, date_from: date, date_to: date) -> list[TargetSummary]:
    """
    Returns one summary record per target: status, total trials, avg accuracy, trend.
    Powers the program overview dashboard — one request for all the target cards.
    """
    targets = list(
        Target.objects
        .filter(program_id=program_id)
        .select_related('prompting_template', 'module', 'submodule')
    )
    if not targets:
        return []

    target_ids = [t.id for t in targets]
    max_scores = _max_scores_for_targets(target_ids)

    raw = list(
        TrialEvent.objects
        .filter(
            target_id__in=target_ids,
            recorded_at__date__gte=date_from,
            recorded_at__date__lte=date_to,
        )
        .values('recorded_at__date', 'target_id', 'response_score', 'session_run_id', 'sub_item_key')
    )

    # Per-target aggregation
    child_series = _sub_item_series(target_ids)
    per_target: dict[int, dict] = defaultdict(lambda: {
        'totals': 0, 'correct': 0, 'sessions': set(), 'dates': [], 'daily_pct': defaultdict(dict),
    })
    for event in raw:
        child = child_series.get((event['target_id'], event.get('sub_item_key') or ''))
        tid = child['series_id'] if child else event['target_id']
        per_target[tid]['totals'] += 1
        per_target[tid]['sessions'].add(event['session_run_id'])
        per_target[tid]['parent_target_id'] = event['target_id']
        if child:
            per_target[tid]['name'] = child['name']
            per_target[tid]['status'] = child['status']
        max_score = max_scores.get(event['target_id'])
        is_correct = (
            event['response_score'] >= max_score if max_score is not None
            else event['response_score'] > 0
        )
        if is_correct:
            per_target[tid]['correct'] += 1
        per_target[tid]['dates'].append(event['recorded_at__date'])
        day_key = event['recorded_at__date']
        per_target[tid]['daily_pct'].setdefault(day_key, {'total': 0, 'correct': 0})
        per_target[tid]['daily_pct'][day_key]['total'] += 1
        if is_correct:
            per_target[tid]['daily_pct'][day_key]['correct'] += 1

    result: list[TargetSummary] = []
    for target in targets:
        tid = target.id
        if any(child['target'].id == target.id for child in child_series.values()):
            continue
        data = per_target.get(tid)
        if not data or data['totals'] == 0:
            result.append({
                'target_id': tid,
                'target_name': target.name,
                'status': target.status,
                'phase': getattr(target, 'phase', None),
                'module_id': target.module_id,
                'module_name': target.module.name if target.module_id else None,
                'submodule_id': target.submodule_id,
                'submodule_name': target.submodule.name if target.submodule_id else None,
                'total_trials': 0,
                'total_sessions': 0,
                'avg_pct_correct': 0.0,
                'last_session_date': None,
                'trend': 'insufficient_data',
            })
            continue

        total = data['totals']
        correct = data['correct']
        avg = round(correct / total * 100, 1) if total else 0.0

        daily_pcts = [
            round(v['correct'] / v['total'] * 100, 1)
            for v in data['daily_pct'].values()
            if v['total'] > 0
        ]
        trend = _compute_trend(sorted(daily_pcts))

        result.append({
            'target_id': tid,
            'target_name': target.name,
            'status': target.status,
            'phase': getattr(target, 'phase', None),
            'module_id': target.module_id,
            'module_name': target.module.name if target.module_id else None,
            'submodule_id': target.submodule_id,
            'submodule_name': target.submodule.name if target.submodule_id else None,
            'total_trials': total,
            'total_sessions': len(data['sessions']),
            'avg_pct_correct': avg,
            'last_session_date': max(data['dates']) if data['dates'] else None,
            'trend': trend,
        })

    for child in child_series.values():
        tid = child['series_id']
        target = child['target']
        data = per_target.get(tid)
        if not data or data['totals'] == 0:
            result.append({
                'target_id': tid,
                'target_name': child['name'],
                'status': child['status'],
                'phase': None,
                'module_id': target.module_id,
                'module_name': target.module.name if target.module_id else None,
                'submodule_id': target.submodule_id,
                'submodule_name': target.submodule.name if target.submodule_id else None,
                'total_trials': 0,
                'total_sessions': 0,
                'avg_pct_correct': 0.0,
                'last_session_date': None,
                'trend': 'insufficient_data',
            })
            continue
        total = data['totals']
        correct = data['correct']
        daily_pcts = [
            round(v['correct'] / v['total'] * 100, 1)
            for v in data['daily_pct'].values()
            if v['total'] > 0
        ]
        result.append({
            'target_id': tid,
            'target_name': child['name'],
            'status': child['status'],
            'phase': None,
            'module_id': target.module_id,
            'module_name': target.module.name if target.module_id else None,
            'submodule_id': target.submodule_id,
            'submodule_name': target.submodule.name if target.submodule_id else None,
            'total_trials': total,
            'total_sessions': len(data['sessions']),
            'avg_pct_correct': round(correct / total * 100, 1) if total else 0.0,
            'last_session_date': max(data['dates']) if data['dates'] else None,
            'trend': _compute_trend(sorted(daily_pcts)),
        })

    return result


# ---------------------------------------------------------------------------
# Module summary — per-module analytics (subset of program summary)
# ---------------------------------------------------------------------------

def get_module_summary(module_id: int, date_from: date, date_to: date) -> list[TargetSummary]:
    """Same as get_program_summary but scoped to a single module."""
    targets = list(
        Target.objects
        .filter(module_id=module_id)
        .select_related('prompting_template', 'module', 'submodule')
    )
    if not targets:
        return []

    target_ids = [t.id for t in targets]
    max_scores = _max_scores_for_targets(target_ids)

    raw = list(
        TrialEvent.objects
        .filter(
            target_id__in=target_ids,
            recorded_at__date__gte=date_from,
            recorded_at__date__lte=date_to,
        )
        .values('recorded_at__date', 'target_id', 'response_score', 'session_run_id')
    )

    per_target: dict[int, dict] = defaultdict(lambda: {
        'totals': 0, 'correct': 0, 'sessions': set(), 'dates': [], 'daily_pct': defaultdict(dict),
    })
    for event in raw:
        tid = event['target_id']
        per_target[tid]['totals'] += 1
        per_target[tid]['sessions'].add(event['session_run_id'])
        max_score = max_scores.get(tid)
        is_correct = (
            event['response_score'] >= max_score if max_score is not None
            else event['response_score'] > 0
        )
        if is_correct:
            per_target[tid]['correct'] += 1
        per_target[tid]['dates'].append(event['recorded_at__date'])
        day_key = event['recorded_at__date']
        per_target[tid]['daily_pct'].setdefault(day_key, {'total': 0, 'correct': 0})
        per_target[tid]['daily_pct'][day_key]['total'] += 1
        if is_correct:
            per_target[tid]['daily_pct'][day_key]['correct'] += 1

    result: list[TargetSummary] = []
    for target in targets:
        tid = target.id
        data = per_target.get(tid)
        if not data or data['totals'] == 0:
            result.append({
                'target_id': tid,
                'target_name': target.name,
                'status': target.status,
                'phase': getattr(target, 'phase', None),
                'module_id': target.module_id,
                'module_name': target.module.name if target.module_id else None,
                'submodule_id': target.submodule_id,
                'submodule_name': target.submodule.name if target.submodule_id else None,
                'total_trials': 0,
                'total_sessions': 0,
                'avg_pct_correct': 0.0,
                'last_session_date': None,
                'trend': 'insufficient_data',
            })
            continue
        total = data['totals']
        correct = data['correct']
        avg = round(correct / total * 100, 1) if total else 0.0
        daily_pcts = [
            round(v['correct'] / v['total'] * 100, 1)
            for v in data['daily_pct'].values() if v['total'] > 0
        ]
        result.append({
            'target_id': tid,
            'target_name': target.name,
            'status': target.status,
            'phase': getattr(target, 'phase', None),
            'module_id': target.module_id,
            'module_name': target.module.name if target.module_id else None,
            'submodule_id': target.submodule_id,
            'submodule_name': target.submodule.name if target.submodule_id else None,
            'total_trials': total,
            'total_sessions': len(data['sessions']),
            'avg_pct_correct': avg,
            'last_session_date': max(data['dates']) if data['dates'] else None,
            'trend': _compute_trend(sorted(daily_pcts)),
        })
    return result


# ---------------------------------------------------------------------------
# Client progress report — all programs + targets in one round-trip
# ---------------------------------------------------------------------------

def get_client_progress_report(
    client_id: int,
    date_from: date,
    date_to: date,
) -> ClientProgressReport:
    """
    Aggregates the full client progress report in 4 DB queries:
    1. Programs + targets for this client
    2. Session counts
    3. All trial events in the date range
    4. Target status change history (mastery events)

    client_id may be either the local DCM Client.id (the staff-facing report
    page's convention) or the TPMS patient id (the caregiver portal's — see
    apps.caregiver_portal.api._scope). Program.external_client_id is always
    the local id, while SessionRun.external_client_id is normalized to the
    TPMS id (see apps.sessions.api._canonical_external_client_id), so both
    forms need resolving here regardless of which one the caller passed in.
    """
    from apps.clients.models import Client
    client = Client.objects.filter(id=client_id).first() or Client.objects.filter(external_id=str(client_id)).first()
    dcm_client_id = client.id if client else client_id
    external_client_id = (
        int(client.external_id) if client and client.external_id and client.external_id.isdigit()
        else dcm_client_id
    )

    # ── 1. Programs and targets ──────────────────────────────────────────────
    programs = list(
        Program.objects
        .filter(external_client_id=dcm_client_id)
        .prefetch_related('targets__prompting_template', 'targets__module', 'targets__submodule')
        .order_by('display_order', 'name')
    )

    all_targets: list[Target] = []
    for program in programs:
        all_targets.extend(program.targets.all())

    target_ids = [t.id for t in all_targets]
    max_scores = _max_scores_for_targets(target_ids)

    # ── 2. Session counts ────────────────────────────────────────────────────
    sessions_qs = SessionRun.objects.filter(
        external_client_id=external_client_id,
        started_at__date__gte=date_from,
        started_at__date__lte=date_to,
    ).values('status')

    total_sessions = 0
    approved_sessions = 0
    submitted_sessions = 0
    for row in sessions_qs:
        total_sessions += 1
        if row['status'] == 'approved':
            approved_sessions += 1
        elif row['status'] == 'submitted':
            submitted_sessions += 1

    # ── 3. Trial events ──────────────────────────────────────────────────────
    raw_trials = list(
        TrialEvent.objects
        .filter(
            target_id__in=target_ids,
            recorded_at__date__gte=date_from,
            recorded_at__date__lte=date_to,
        )
        .values('target_id', 'target_name', 'response_score',
                'recorded_at__date', 'session_run_id')
    )

    # Aggregate per target
    per_target: dict[int, dict] = defaultdict(lambda: {
        'totals': 0, 'correct': 0,
        'sessions': set(), 'dates': [],
        'daily_pct': defaultdict(lambda: {'total': 0, 'correct': 0}),
    })
    for event in raw_trials:
        tid = event['target_id']
        per_target[tid]['totals'] += 1
        per_target[tid]['sessions'].add(event['session_run_id'])
        per_target[tid]['dates'].append(event['recorded_at__date'])
        max_score = max_scores.get(tid)
        is_correct = (
            event['response_score'] >= max_score if max_score is not None
            else event['response_score'] > 0
        )
        if is_correct:
            per_target[tid]['correct'] += 1
        day = event['recorded_at__date']
        per_target[tid]['daily_pct'][day]['total'] += 1
        if is_correct:
            per_target[tid]['daily_pct'][day]['correct'] += 1

    # ── 4. Build per-program report ──────────────────────────────────────────
    mastered_targets = 0
    total_targets = len(all_targets)
    program_reports: list[ProgramReport] = []

    # Group targets by program
    targets_by_program: dict[int, list[Target]] = defaultdict(list)
    for t in all_targets:
        targets_by_program[t.program_id].append(t)

    for program in programs:
        target_summaries: list[TargetSummary] = []

        for target in targets_by_program.get(program.id, []):
            tid = target.id
            if target.status == 'mastered':
                mastered_targets += 1

            data = per_target.get(tid)
            if not data or data['totals'] == 0:
                target_summaries.append({
                    'target_id': tid,
                    'target_name': target.name,
                    'status': target.status,
                    'phase': getattr(target, 'phase', None),
                    'module_id': target.module_id,
                    'module_name': target.module.name if target.module_id else None,
                    'submodule_id': target.submodule_id,
                    'submodule_name': target.submodule.name if target.submodule_id else None,
                    'total_trials': 0,
                    'total_sessions': 0,
                    'avg_pct_correct': 0.0,
                    'last_session_date': None,
                    'trend': 'insufficient_data',
                })
                continue

            total = data['totals']
            correct = data['correct']
            avg = round(correct / total * 100, 1) if total else 0.0
            daily_pcts = [
                round(v['correct'] / v['total'] * 100, 1)
                for v in data['daily_pct'].values()
                if v['total'] > 0
            ]
            target_summaries.append({
                'target_id': tid,
                'target_name': target.name,
                'status': target.status,
                'phase': getattr(target, 'phase', None),
                'module_id': target.module_id,
                'module_name': target.module.name if target.module_id else None,
                'submodule_id': target.submodule_id,
                'submodule_name': target.submodule.name if target.submodule_id else None,
                'total_trials': total,
                'total_sessions': len(data['sessions']),
                'avg_pct_correct': avg,
                'last_session_date': max(data['dates']) if data['dates'] else None,
                'trend': _compute_trend(sorted(daily_pcts)),
            })

        program_reports.append({
            'program_id': program.id,
            'program_name': program.name,
            'category': program.category,
            'treatment_area': program.treatment_area,
            'status': program.status,
            'targets': target_summaries,
        })

    active_programs = sum(1 for p in programs if p.status == 'active')

    return {
        'client_id': client_id,
        'date_from': date_from,
        'date_to': date_to,
        'total_sessions': total_sessions,
        'approved_sessions': approved_sessions,
        'submitted_sessions': submitted_sessions,
        'total_programs': len(programs),
        'active_programs': active_programs,
        'mastered_targets': mastered_targets,
        'total_targets': total_targets,
        'programs': program_reports,
    }


# ---------------------------------------------------------------------------
# Client progress overview — cumulative mastery timeline + per-program rollup,
# consolidated across every program for one client (powers the Progress screen)
# ---------------------------------------------------------------------------

def get_client_progress_overview(client_id: int) -> ClientProgressOverview:
    """
    Two things, computed across ALL of a client's programs at once:
    1. mastery_events — every target's transition into 'mastered', with its
       program/treatment-area/tags/status attached, so the frontend can
       bucket (day/week/month), filter (by tag/status), and toggle
       cumulative vs. per-period entirely client-side without another round
       trip — same source data powers both the "Accumulated" chart and the
       "Recently Mastered" list.
    2. programs — per-program rollup: current target counts by status, plus
       avg trials/sessions to mastery (lifetime — mastery is a point-in-time
       fact, not a report-window metric).

    Mirrors get_client_progress_report's client_id resolution (accepts either
    the local Client.id or the TPMS external id).
    """
    from apps.clients.models import Client
    from apps.programs.models import TargetStatusChange

    client = Client.objects.filter(id=client_id).first() or Client.objects.filter(external_id=str(client_id)).first()
    dcm_client_id = client.id if client else client_id

    programs = list(
        Program.objects
        .filter(external_client_id=dcm_client_id)
        .prefetch_related('targets')
        .order_by('display_order', 'name')
    )

    all_targets: list[Target] = []
    for program in programs:
        all_targets.extend(program.targets.all())
    target_ids = [t.id for t in all_targets]

    targets_by_program: dict[int, list[Target]] = defaultdict(list)
    for t in all_targets:
        targets_by_program[t.program_id].append(t)

    # Earliest transition into 'mastered' per target — 'mastered' is the
    # built-in seed status key (see Target model comment); same convention
    # already used by get_client_progress_report's mastered_targets count.
    mastery_ts: dict[int, 'date'] = {}
    for change in (
        TargetStatusChange.objects
        .filter(target_id__in=target_ids, to_status='mastered')
        .order_by('target_id', 'created_at')
        .values('target_id', 'created_at')
    ):
        mastery_ts.setdefault(change['target_id'], change['created_at'])

    # Trials/sessions up to each target's mastery timestamp, in one query.
    trials_by_target: dict[int, list[dict]] = defaultdict(list)
    for row in (
        TrialEvent.objects
        .filter(target_id__in=mastery_ts.keys())
        .values('target_id', 'recorded_at', 'session_run_id')
    ):
        trials_by_target[row['target_id']].append(row)

    trials_to_mastery: dict[int, int] = {}
    sessions_to_mastery: dict[int, int] = {}
    for tid, mastered_at in mastery_ts.items():
        rows = [r for r in trials_by_target.get(tid, []) if r['recorded_at'] <= mastered_at]
        trials_to_mastery[tid] = len(rows)
        sessions_to_mastery[tid] = len({r['session_run_id'] for r in rows})

    # ── Mastery events (raw — frontend buckets/filters/cumulates) ───────────
    targets_by_id = {t.id: t for t in all_targets}
    programs_by_id = {p.id: p for p in programs}
    mastery_events: list[MasteryEvent] = []
    for tid, mastered_at in mastery_ts.items():
        target = targets_by_id[tid]
        program = programs_by_id[target.program_id]
        mastery_events.append({
            'target_id': tid,
            'target_name': target.name,
            'program_id': program.id,
            'program_name': program.name,
            'treatment_area': program.treatment_area,
            'program_status': program.status,
            'program_tags': program.tags or [],
            'mastered_at': mastered_at,
        })
    mastery_events.sort(key=lambda e: e['mastered_at'])

    # ── Per-program rollup ─────────────────────────────────────────────────
    program_stats: list[ProgramProgressStats] = []
    for program in programs:
        targets = targets_by_program.get(program.id, [])
        status_counts: dict[str, int] = defaultdict(int)
        for t in targets:
            status_counts[t.status] += 1

        mastered_ids = [t.id for t in targets if t.id in trials_to_mastery]
        avg_trials = (
            round(sum(trials_to_mastery[tid] for tid in mastered_ids) / len(mastered_ids), 1)
            if mastered_ids else None
        )
        avg_sessions = (
            round(sum(sessions_to_mastery[tid] for tid in mastered_ids) / len(mastered_ids), 1)
            if mastered_ids else None
        )

        program_stats.append({
            'program_id': program.id,
            'program_name': program.name,
            'treatment_area': program.treatment_area,
            'status': program.status,
            'tags': program.tags or [],
            'status_counts': dict(status_counts),
            'avg_trials_to_mastery': avg_trials,
            'avg_sessions_to_mastery': avg_sessions,
        })

    return {
        'client_id': client_id,
        'mastery_events': mastery_events,
        'programs': program_stats,
    }
