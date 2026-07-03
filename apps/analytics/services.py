from collections import defaultdict
from datetime import date
from typing import TypedDict

from apps.programs.models import Program, Target
from apps.sessions.models import TrialEvent, BehaviorEvent, ABCEvent


# ---------------------------------------------------------------------------
# Type hints for aggregated data points
# ---------------------------------------------------------------------------

class TrialDataPoint(TypedDict):
    date: date
    target_id: int
    target_name: str
    total_trials: int
    correct_count: int
    pct_correct: float


class BehaviorDataPoint(TypedDict):
    date: date
    target_id: int
    target_name: str
    frequency: int
    total_duration_seconds: int


class TargetSummary(TypedDict):
    target_id: int
    target_name: str
    status: str
    total_trials: int
    total_sessions: int
    avg_pct_correct: float
    last_session_date: date | None
    trend: str      # improving | declining | stable | insufficient_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _max_scores_for_targets(target_ids: list[int]) -> dict[int, int | None]:
    """Returns {target_id: max_prompting_score} for the given target IDs."""
    result: dict[int, int | None] = {}
    for target in Target.objects.filter(id__in=target_ids).select_related('prompting_template'):
        if target.prompting_template and target.prompting_template.levels:
            result[target.id] = max(lvl['score'] for lvl in target.prompting_template.levels)
        else:
            result[target.id] = None
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
) -> list[TrialDataPoint]:
    """
    Returns daily trial accuracy per target between two dates.
    Suitable for rendering per-target accuracy lines on a program graph.
    """
    if not target_ids:
        return []

    max_scores = _max_scores_for_targets(target_ids)

    raw = list(
        TrialEvent.objects
        .filter(
            target_id__in=target_ids,
            recorded_at__date__gte=date_from,
            recorded_at__date__lte=date_to,
        )
        .values('recorded_at__date', 'target_id', 'target_name', 'response_score')
    )

    # Aggregate in Python — avoids N+1 and per-target subqueries
    grouped: dict[tuple, dict] = defaultdict(lambda: {'total': 0, 'correct': 0, 'name': ''})
    for event in raw:
        key = (event['recorded_at__date'], event['target_id'])
        grouped[key]['total'] += 1
        grouped[key]['name'] = event['target_name']
        max_score = max_scores.get(event['target_id'])
        is_correct = (
            event['response_score'] >= max_score if max_score is not None
            else event['response_score'] > 0
        )
        if is_correct:
            grouped[key]['correct'] += 1

    result: list[TrialDataPoint] = []
    for (day, tid), data in sorted(grouped.items()):
        total = data['total']
        correct = data['correct']
        result.append({
            'date': day,
            'target_id': tid,
            'target_name': data['name'],
            'total_trials': total,
            'correct_count': correct,
            'pct_correct': round(correct / total * 100, 1) if total else 0.0,
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

    grouped: dict[tuple, dict] = defaultdict(lambda: {'freq': 0, 'dur': 0, 'name': ''})
    for event in raw:
        key = (event['occurred_at__date'], event['target_id'])
        grouped[key]['freq'] += event['frequency_count']
        grouped[key]['dur'] += event['duration_seconds'] or 0
        grouped[key]['name'] = event['target_name']

    return [
        {
            'date': day,
            'target_id': tid,
            'target_name': data['name'],
            'frequency': data['freq'],
            'total_duration_seconds': data['dur'],
        }
        for (day, tid), data in sorted(grouped.items())
    ]


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
        .select_related('prompting_template')
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

    # Per-target aggregation
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
            'total_trials': total,
            'total_sessions': len(data['sessions']),
            'avg_pct_correct': avg,
            'last_session_date': max(data['dates']) if data['dates'] else None,
            'trend': trend,
        })

    return result
