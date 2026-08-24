from collections import defaultdict
from datetime import date
from typing import TypedDict

from apps.programs.models import Program, Target, ProgramModule, ProgramSubmodule, TargetSubItem
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
        .values('occurred_at__date', 'target_id', 'target_name', 'frequency_count', 'duration_seconds')
    )

    target_meta: dict[int, dict] = {
        t.id: {'module_id': t.module_id, 'submodule_id': t.submodule_id}
        for t in Target.objects.filter(id__in=target_ids).only('id', 'module_id', 'submodule_id')
    }
    mod_ids = {m['module_id'] for m in target_meta.values() if m['module_id']}
    sub_ids = {m['submodule_id'] for m in target_meta.values() if m['submodule_id']}
    mod_names = _module_name_map(mod_ids)
    sub_names = _submodule_name_map(sub_ids)

    grouped: dict[tuple, dict] = defaultdict(lambda: {'freq': 0, 'dur': 0, 'name': ''})
    for event in raw:
        key = (event['occurred_at__date'], event['target_id'])
        grouped[key]['freq'] += event['frequency_count']
        grouped[key]['dur'] += event['duration_seconds'] or 0
        grouped[key]['name'] = event['target_name']

    result: list[BehaviorDataPoint] = []
    for (day, tid), data in sorted(grouped.items()):
        meta = target_meta.get(tid, {})
        mid = meta.get('module_id')
        sid = meta.get('submodule_id')
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
