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
) -> float:
    """Roll a set of observations up into the target's configured metric.

    - durations: observed duration_seconds values (duration measurements)
    - event_count: total occurrences — sum of frequency_count (rate/frequency)
    - seconds_elapsed: session/observation window length, for rate measurements

    Returns 0.0 when there is nothing to aggregate.
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
        if not event_count:
            return 0.0
        if not seconds_elapsed or seconds_elapsed <= 0:
            return float(event_count)
        hours = max(seconds_elapsed / 3600, 1 / 60)
        if measurement == M.RATE_PER_MINUTE:
            return float(event_count / (hours * 60))
        return float(event_count / hours)

    return float(event_count)
