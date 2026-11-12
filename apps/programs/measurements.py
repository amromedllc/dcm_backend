"""Shared logic for turning raw session observations into a target's
configured `measurement` value.

`Target.measurement` (see models.py) picks which metric a duration / rate /
frequency target's BehaviorEvent rows roll up into. Both the workflow/mastery
engine (apps.programs.services) and the progress charts (apps.analytics.services)
go through here so the number a supervisor sees on a graph is the same number
the automation evaluates against.
"""
from __future__ import annotations

from .models import Target

M = Target.Measurement

# Human-readable metric name + unit, for chart axis / session-summary labels.
MEASUREMENT_LABELS: dict[str, str] = {
    M.PERCENT_CORRECT: 'Percent Correct',
    M.FREQUENCY: 'Frequency',
    M.RATE_PER_HOUR: 'Rate / hour',
    M.RATE_PER_MINUTE: 'Rate / minute',
    M.TOTAL_OBSERVED_DURATION: 'Total Observed Duration',
    M.MIN_OBSERVED_DURATION: 'Min. Observed Duration',
    M.MAX_OBSERVED_DURATION: 'Max. Observed Duration',
    M.AVG_OBSERVED_DURATION: 'Avg. Observed Duration',
}

MEASUREMENT_UNIT: dict[str, str] = {
    M.PERCENT_CORRECT: '%',
    M.FREQUENCY: 'count',
    M.RATE_PER_HOUR: '/hr',
    M.RATE_PER_MINUTE: '/min',
    M.TOTAL_OBSERVED_DURATION: 's',
    M.MIN_OBSERVED_DURATION: 's',
    M.MAX_OBSERVED_DURATION: 's',
    M.AVG_OBSERVED_DURATION: 's',
}

_MEASUREMENT_OBJECTIVE_KEY: dict[str, str] = {
    M.PERCENT_CORRECT: '',
    M.FREQUENCY: 'frequency',
    M.RATE_PER_HOUR: 'rate_per_hour',
    M.RATE_PER_MINUTE: 'rate_per_minute',
    M.TOTAL_OBSERVED_DURATION: 'total_duration',
    M.MIN_OBSERVED_DURATION: 'min_duration',
    M.MAX_OBSERVED_DURATION: 'max_duration',
    M.AVG_OBSERVED_DURATION: 'avg_duration',
}

DURATION_MEASUREMENTS = frozenset({
    M.TOTAL_OBSERVED_DURATION, M.MIN_OBSERVED_DURATION,
    M.MAX_OBSERVED_DURATION, M.AVG_OBSERVED_DURATION,
})
RATE_MEASUREMENTS = frozenset({M.RATE_PER_HOUR, M.RATE_PER_MINUTE})

BEHAVIOR_STYLE_MEASUREMENTS = DURATION_MEASUREMENTS | RATE_MEASUREMENTS | frozenset({M.FREQUENCY})


def objective_key_for_measurement(measurement: str) -> str:
    """The workflow-criteria objective key a bare `measurement` maps to, so a
    duration/rate/frequency target is still evaluated correctly when its
    WorkflowTemplate phase doesn't set an explicit `objective_key`."""
    return _MEASUREMENT_OBJECTIVE_KEY.get(measurement, '')


def aggregate_measurement(
    measurement: str,
    *,
    durations: list[float] | None = None,
    event_count: int = 0,
    seconds_elapsed: float | None = None,
) -> float | None:
    """Roll a set of observations up into the target's configured metric.

    - durations: observed duration_seconds values (duration measurements)
    - event_count: total occurrences — sum of frequency_count (rate/frequency)
    - seconds_elapsed: session/observation window length, for rate measurements

    Returns 0.0 when there is nothing to aggregate, except that rates without
    a positive observation denominator remain unknown (None).
    """
    durations = [d for d in (durations or []) if d is not None]

    if measurement in DURATION_MEASUREMENTS:
        if not durations:
            return 0.0
        if measurement == M.MIN_OBSERVED_DURATION:
            return float(min(durations))
        if measurement == M.MAX_OBSERVED_DURATION:
            return float(max(durations))
        if measurement == M.AVG_OBSERVED_DURATION:
            return float(sum(durations) / len(durations))
        return float(sum(durations))  # total

    if measurement in RATE_MEASUREMENTS:
        if not seconds_elapsed or seconds_elapsed <= 0:
            return None
        return float(event_count * (60 if measurement == M.RATE_PER_MINUTE else 3600) / seconds_elapsed)

    return float(event_count)


def merged_periods(rows, lower=None, upper=None):
    periods = []
    for row in rows:
        end = row.get('observation_ended_at')
        if end is None:
            continue
        start, end = row['occurred_at'].timestamp(), end.timestamp()
        if lower is not None:
            start = max(start, lower.timestamp())
        if upper is not None:
            end = min(end, upper.timestamp())
        if end > start:
            periods.append((start, end))
    merged = []
    for start, end in sorted(periods):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def rate_statistics(rows, *, lower=None, upper=None, legacy_window=None):
    """Count occurrences only inside observed periods; merge overlaps once.

    The end boundary is inclusive for occurrences, so stopping immediately
    after a tally does not lose that occurrence. Merged periods prevent double
    counting at adjacent boundaries. Unknown coverage is distinct from zero.
    """
    periods = merged_periods(rows, lower, upper)
    has_periods = any(row.get('observation_ended_at') is not None for row in rows)
    if not has_periods and legacy_window:
        start, end = legacy_window
        if start and end and end.timestamp() > start.timestamp():
            start, end = start.timestamp(), end.timestamp()
            start = max(start, lower.timestamp()) if lower is not None else start
            end = min(end, upper.timestamp()) if upper is not None else end
            periods = [(start, end)] if end > start else []
    seconds = sum(end - start for start, end in periods)
    count = sum(row['frequency_count'] for row in rows
                if row.get('observation_ended_at') is None
                and (upper is None or row['occurred_at'].timestamp() < upper.timestamp())
                and any(start <= row['occurred_at'].timestamp() <= end for start, end in periods))
    return count, seconds if seconds > 0 else None


def uses_target_windows(snapshot, target_id):
    return any(target.get('id') == target_id and target.get('rate_observation_mode') == 'target_windows'
               for program in snapshot.get('programs', []) for target in program.get('targets', []))


def session_rate_statistics(session, target_id, rows):
    legacy = None
    if not uses_target_windows(session.program_snapshot, target_id):
        legacy = (session.started_at, session.ended_at or session.submitted_at or session.reviewed_at)
    return rate_statistics(rows, legacy_window=legacy)
