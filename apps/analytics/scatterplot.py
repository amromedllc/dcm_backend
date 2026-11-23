"""Occurrence counts and explicit observation coverage from session snapshots."""
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone as utc
from functools import lru_cache

from django.db.models import Q
from django.utils import timezone

from apps.sessions.models import BehaviorEvent, SessionRun
from shared.tenancy import current_org_id


@lru_cache(maxsize=16)
def _time_segments(date_from, date_to, bin_minutes, zone):
    """UTC spans of each local clock bucket, including repeated/skipped DST hours.

    Minute-sized steps keep timezone transitions correct; coalescing produces at
    most one span per normal bucket. This cache contains only calendar metadata.
    """
    start = timezone.make_aware(datetime.combine(date_from, time.min), zone).astimezone(utc.utc)
    end = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min), zone).astimezone(utc.utc)
    cursor = start
    segments = []
    while cursor < end:
        local = cursor.astimezone(zone)
        key = (local.weekday(), (local.hour * 60 + local.minute) // bin_minutes * bin_minutes)
        next_at = min(cursor + timedelta(minutes=1), end)
        if segments and segments[-1][2] == key and segments[-1][1] == cursor:
            segments[-1] = (segments[-1][0], next_at, key)
        else:
            segments.append((cursor, next_at, key))
        cursor = next_at
    return tuple(segments)


def _merge_periods(periods):
    merged = []
    for start, end in sorted(periods):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def bucket_scatterplot(events, date_from: date, date_to: date, bin_minutes: int, zone, target_ids=None):
    """Coverage is the union per target, never inferred from session length or events."""
    if bin_minutes not in (30, 60):
        raise ValueError('Scatterplot bins must be 30 or 60 minutes')
    if date_from > date_to:
        raise ValueError('Start date must be on or before end date')
    events = list(events)
    targets = set(target_ids) if target_ids is not None else {e['target_id'] for e in events}
    counts, observed, expected = defaultdict(int), defaultdict(float), defaultdict(float)
    segments = _time_segments(date_from, date_to, bin_minutes, zone)
    starts = [span[0] for span in segments]
    periods = defaultdict(list)
    for start, end, key in segments:
        expected[key] += (end - start).total_seconds() * len(targets)
    for event in events:
        if event['target_id'] not in targets:
            continue
        start = event['occurred_at'].astimezone(utc.utc)
        end = event.get('observation_ended_at')
        if end is not None:
            periods[event['target_id']].append((max(start, segments[0][0]), min(end.astimezone(utc.utc), segments[-1][1])))
            continue
        local = start.astimezone(zone)
        if date_from <= local.date() <= date_to:
            minute = (local.hour * 60 + local.minute) // bin_minutes * bin_minutes
            counts[(local.weekday(), minute)] += event['frequency_count']
    for windows in periods.values():
        for start, end in _merge_periods([(a, b) for a, b in windows if a < b]):
            index = max(0, bisect_right(starts, start) - 1)
            while index < len(segments) and segments[index][0] < end:
                bucket_start, bucket_end, key = segments[index]
                observed[key] += max(0, (min(end, bucket_end) - max(start, bucket_start)).total_seconds())
                index += 1
    cells = []
    for minute in range(0, 1440, bin_minutes):
        for weekday in range(7):
            key = (weekday, minute)
            covered, possible = observed[key], expected[key]
            state = 'observed' if possible > 0 and covered >= possible else 'partial' if covered > 0 else 'unobserved'
            cells.append({
                'weekday': weekday, 'minute_of_day': minute,
                'count': counts[key] if covered > 0 or counts[key] else None,
                'observation_state': state, 'observed_seconds': covered, 'expected_seconds': possible,
            })
    return cells


def get_scatterplot_data(target_ids, client_ids, date_from, date_to, bin_minutes):
    zone = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(date_from, time.min), zone)
    end = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min), zone)
    # Include observed periods that start before the requested date range.
    events = list(BehaviorEvent.objects.filter(
        Q(occurred_at__gte=start) | Q(observation_ended_at__gt=start),
        target_id__in=target_ids, occurred_at__lt=end,
        session_run__external_client_id__in=client_ids,
        session_run__organization_id=current_org_id(),
    ).values('session_run_id', 'target_id', 'occurred_at', 'frequency_count', 'observation_ended_at'))
    session_targets = {}
    for session in SessionRun.objects.filter(id__in={e['session_run_id'] for e in events}).values('id', 'program_snapshot'):
        session_targets[session['id']] = {
            target['id'] for program in session['program_snapshot'].get('programs', [])
            for target in program.get('targets', []) if target.get('measurement_type') == 'scatterplot'
        }
    eligible = [e for e in events if e['target_id'] in session_targets.get(e['session_run_id'], set())]
    cells = bucket_scatterplot(eligible, date_from, date_to, bin_minutes, zone, target_ids)
    return {
        'date_from': date_from, 'date_to': date_to, 'timezone': str(zone),
        'bin_minutes': bin_minutes, 'total_occurrences': sum(cell['count'] or 0 for cell in cells),
        'total_observed_seconds': sum(cell['observed_seconds'] for cell in cells), 'cells': cells,
    }
