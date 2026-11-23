"""Pure helpers for chart observation denominators (no database access)."""
from datetime import datetime, time, timedelta


def session_seconds_on_day(start, end, day, zone):
    """Use the completed session window, clipped to the recorded local day.

    Unknown windows cannot produce a rate. Do not substitute the distance
    between occurrences: that is not an observation denominator.
    """
    if start is None or end is None or end.timestamp() <= start.timestamp():
        return None
    lower = datetime.combine(day, time.min, tzinfo=zone)
    upper = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    seconds = min(end.timestamp(), upper.timestamp()) - max(start.timestamp(), lower.timestamp())
    return seconds if seconds > 0 else None


def chart_rate(count, seconds, per_hour=False):
    if seconds is None or seconds <= 0:
        return None
    return count * (3600 if per_hour else 60) / seconds


def session_window_statistics(session_id, start, end, day, zone, date_from=None, date_to=None):
    """Denominators per calendar bucket, deduplicated by session in the client.

    Include the full applicable week/month window even if all occurrences were
    on one day. Silent portions of the same session still belong in the rate.
    """
    week = day - timedelta(days=day.weekday())
    month = day.replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)

    def seconds_between(first, after):
        if date_from is not None:
            first = max(first, date_from)
        if date_to is not None:
            after = min(after, date_to + timedelta(days=1))
        if start is None or end is None or end.timestamp() <= start.timestamp():
            return None
        lower = datetime.combine(first, time.min, tzinfo=zone).timestamp()
        upper = datetime.combine(after, time.min, tzinfo=zone).timestamp()
        seconds = min(end.timestamp(), upper) - max(start.timestamp(), lower)
        return seconds if seconds > 0 else None

    return {
        'session_id': session_id,
        'daily': seconds_between(day, day + timedelta(days=1)),
        'weekly': seconds_between(week, week + timedelta(days=7)),
        'monthly': seconds_between(month, next_month),
        'session': seconds_between(date_from, date_to + timedelta(days=1)) if date_from and date_to else (end.timestamp() - start.timestamp() if start and end and end.timestamp() > start.timestamp() else None),
    }
