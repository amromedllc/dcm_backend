"""Weekday/time-of-day counts from immutable scatterplot session snapshots."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from apps.sessions.models import BehaviorEvent, SessionRun
from shared.tenancy import current_org_id


def bucket_scatterplot(events, date_from: date, date_to: date, bin_minutes: int, zone):
    """Pure aggregation; empty buckets indicate no recorded occurrences, not absence."""
    if bin_minutes not in (30, 60):
        raise ValueError('Scatterplot bins must be 30 or 60 minutes')
    if date_from > date_to:
        raise ValueError('Start date must be on or before end date')
    counts = defaultdict(int)
    for event in events:
        local = timezone.localtime(event['occurred_at'], zone)
        if not date_from <= local.date() <= date_to:
            continue
        minute = (local.hour * 60 + local.minute) // bin_minutes * bin_minutes
        counts[(local.weekday(), minute)] += event['frequency_count']
    return [
        {'weekday': weekday, 'minute_of_day': minute, 'count': counts[(weekday, minute)]}
        for minute in range(0, 1440, bin_minutes)
        for weekday in range(7)
    ]


def get_scatterplot_data(target_ids, client_ids, date_from, date_to, bin_minutes):
    zone = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(date_from, time.min), zone)
    end = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min), zone)
    events = list(BehaviorEvent.objects.filter(
        target_id__in=target_ids,
        occurred_at__gte=start, occurred_at__lt=end,
        session_run__external_client_id__in=client_ids,
        session_run__organization_id=current_org_id(),
    ).values('session_run_id', 'target_id', 'occurred_at', 'frequency_count'))
    # Do not reinterpret older frequency/rate events after a target's type changes.
    # The historical session snapshot, not the current target, determines eligibility.
    session_targets = {}
    for session in SessionRun.objects.filter(id__in={e['session_run_id'] for e in events}).values('id', 'program_snapshot'):
        session_targets[session['id']] = {
            target['id']
            for program in session['program_snapshot'].get('programs', [])
            for target in program.get('targets', [])
            if target.get('measurement_type') == 'scatterplot'
        }
    eligible = [e for e in events if e['target_id'] in session_targets.get(e['session_run_id'], set())]
    cells = bucket_scatterplot(eligible, date_from, date_to, bin_minutes, zone)
    return {
        'date_from': date_from, 'date_to': date_to, 'timezone': str(zone),
        'bin_minutes': bin_minutes, 'total_occurrences': sum(cell['count'] for cell in cells),
        'cells': cells,
    }
