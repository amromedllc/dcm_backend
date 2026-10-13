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
    M.AVG_LATENCY_SECONDS: 'Avg. Latency',
    M.MIN_LATENCY_SECONDS: 'Min. Latency',
    M.MAX_LATENCY_SECONDS: 'Max. Latency',
    M.PERCENT_INTERVALS: 'Percent of Intervals',
    M.PERCENT_YES: 'Percent Yes',
    M.AVG_RATING: 'Avg. Rating',
    M.FIDELITY_PERCENT: 'Fidelity %',
    M.PERCENT_OF_DENOMINATOR: 'Percentage',
    M.TOTAL_PRODUCT_COUNT: 'Total Product Count',
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
    M.AVG_LATENCY_SECONDS: 's',
    M.MIN_LATENCY_SECONDS: 's',
    M.MAX_LATENCY_SECONDS: 's',
    M.PERCENT_INTERVALS: '%',
    M.PERCENT_YES: '%',
    M.AVG_RATING: 'rating',
    M.FIDELITY_PERCENT: '%',
    M.PERCENT_OF_DENOMINATOR: '%',
    M.TOTAL_PRODUCT_COUNT: 'count',
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
    M.AVG_LATENCY_SECONDS: 'avg_latency',
    M.MIN_LATENCY_SECONDS: 'min_latency',
    M.MAX_LATENCY_SECONDS: 'max_latency',
    M.PERCENT_INTERVALS: 'percent_intervals',
    M.PERCENT_YES: 'percent_yes',
    M.AVG_RATING: 'avg_rating',
    M.FIDELITY_PERCENT: 'fidelity_percent',
    M.PERCENT_OF_DENOMINATOR: 'percent_of_denominator',
    M.TOTAL_PRODUCT_COUNT: 'total_product_count',
}

DURATION_MEASUREMENTS = frozenset({
    M.TOTAL_OBSERVED_DURATION, M.MIN_OBSERVED_DURATION,
    M.MAX_OBSERVED_DURATION, M.AVG_OBSERVED_DURATION,
})
RATE_MEASUREMENTS = frozenset({M.RATE_PER_HOUR, M.RATE_PER_MINUTE})
LATENCY_MEASUREMENTS = frozenset({M.AVG_LATENCY_SECONDS, M.MIN_LATENCY_SECONDS, M.MAX_LATENCY_SECONDS})
INTERVAL_MEASUREMENTS = frozenset({M.PERCENT_INTERVALS})
RATING_MEASUREMENTS = frozenset({M.AVG_RATING})
PERCENTAGE_MEASUREMENTS = frozenset({M.PERCENT_OF_DENOMINATOR})
PRODUCT_MEASUREMENTS = frozenset({M.TOTAL_PRODUCT_COUNT})

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
    latencies: list[float] | None = None,
    ratings: list[float] | None = None,
    observed_count: int = 0,
    occurred_count: int = 0,
    numerator: float = 0,
    denominator: float | None = None,
) -> float:
    """Roll a set of observations up into the target's configured metric.

    - durations: observed duration_seconds values (duration measurements)
    - event_count: total occurrences — sum of frequency_count (rate/frequency/
      permanent_product)
    - seconds_elapsed: session/observation window length, for rate measurements
    - latencies: per-trial cue-to-response seconds, excluding censored timeouts
    - ratings: per-trial raw rating-scale values
    - observed_count/occurred_count: for whole/partial interval and momentary
      time sampling — observed_count is the number of observed intervals or
      samples, occurred_count the number where the behavior occurred
    - numerator/denominator: for a generic `percentage` target

    Returns 0.0 when there is nothing to aggregate.
    """
    durations = [d for d in (durations or []) if d is not None]
    latencies = [v for v in (latencies or []) if v is not None]
    ratings = [v for v in (ratings or []) if v is not None]

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

    if measurement in LATENCY_MEASUREMENTS:
        if not latencies:
            return 0.0
        if measurement == M.MIN_LATENCY_SECONDS:
            return float(min(latencies))
        if measurement == M.MAX_LATENCY_SECONDS:
            return float(max(latencies))
        return float(sum(latencies) / len(latencies))  # avg

    if measurement in RATING_MEASUREMENTS:
        if not ratings:
            return 0.0
        return float(sum(ratings) / len(ratings))

    if measurement in INTERVAL_MEASUREMENTS:
        if not observed_count:
            return 0.0
        return occurred_count / observed_count * 100

    if measurement in PERCENTAGE_MEASUREMENTS:
        if not denominator:
            return 0.0
        return numerator / denominator * 100

    if measurement in PRODUCT_MEASUREMENTS:
        return float(event_count)

    return float(event_count)
