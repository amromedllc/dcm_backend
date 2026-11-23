"""Aggregate reporting over the caller's current authorized client cohort."""
from collections import defaultdict
from datetime import datetime, time, timedelta
from django.db.models import Count, Q, Min
from django.db.models.functions import TruncWeek, TruncMonth
from django.utils import timezone
from apps.accounts.models import User
from apps.accounts.api import _same_practice_q
from apps.clients.api import _get_accessible_clients
from apps.clients.models import Client, ClientStatusObservation
from apps.sessions.models import SessionRun
from apps.notes.models import LessonNote
from apps.programs.models import Target, TargetStatusChange


def _period(day, group_by):
    return day - timedelta(days=day.weekday()) if group_by == 'week' else day.replace(day=1)


def _next_period(day, group_by):
    if group_by == 'week':
        return day + timedelta(days=7)
    return day.replace(year=day.year + 1, month=1) if day.month == 12 else day.replace(month=day.month + 1)


def _percent(numerator, denominator):
    return round(100 * numerator / denominator, 1) if denominator else None


def _safe_identifiers(clients):
    """Legacy IDs can be local or external. Ambiguous cross-scope IDs fail closed."""
    visible = {c['id'] for c in clients}
    ownership = defaultdict(set)
    for client_id, external_id in Client.objects.values_list('id', 'external_id'):
        ownership[client_id].add(client_id)
        if external_id and external_id.isdigit():
            ownership[int(external_id)].add(client_id)
    accepted = {identifier for identifier, owners in ownership.items() if len(owners) == 1 and owners <= visible}
    excluded = sum(bool(owners & visible) and len(owners) > 1 for owners in ownership.values())
    return accepted, excluded


def _aging(qs, now):
    valid = Q(submitted_at__lte=now)
    result = qs.aggregate(
        total=Count('pk'),
        under_1_day=Count('pk', filter=valid & Q(submitted_at__gt=now - timedelta(days=1))),
        days_1_to_3=Count('pk', filter=Q(submitted_at__lte=now - timedelta(days=1), submitted_at__gt=now - timedelta(days=3))),
        days_3_to_7=Count('pk', filter=Q(submitted_at__lte=now - timedelta(days=3), submitted_at__gt=now - timedelta(days=7))),
        days_7_plus=Count('pk', filter=Q(submitted_at__lte=now - timedelta(days=7))),
        unknown=Count('pk', filter=Q(submitted_at__isnull=True) | Q(submitted_at__gt=now)),
        oldest=Min('submitted_at', filter=valid),
    )
    oldest = result.pop('oldest')
    result['oldest_wait_days'] = round((now - oldest).total_seconds() / 86400, 1) if oldest else None
    return result


def get_organization_report(request, date_from, date_to, group_by, zone):
    now = timezone.now()
    start = datetime.combine(date_from, time.min, zone)
    end = datetime.combine(date_to + timedelta(days=1), time.min, zone)
    clients = list(_get_accessible_clients(request).values('id', 'external_id', 'status', 'created_at'))
    ids, ambiguous = _safe_identifiers(clients)
    sessions = SessionRun.objects.filter(external_client_id__in=ids, started_at__gte=start, started_at__lt=end)
    counts = sessions.aggregate(total=Count('pk'), approved=Count('pk', filter=Q(status='approved')),
                                rejected=Count('pk', filter=Q(status='rejected')), pending=Count('pk', filter=Q(status='submitted')),
                                open=Count('pk', filter=Q(status='open')))
    counts['approval_rate'] = _percent(counts['approved'], counts['approved'] + counts['rejected'])
    trunc = TruncWeek if group_by == 'week' else TruncMonth
    bucket_counts = {r['bucket'].date(): r['total'] for r in sessions.annotate(bucket=trunc('started_at', tzinfo=zone)).values('bucket').annotate(total=Count('pk')).order_by('bucket')}
    periods = []
    cursor = _period(date_from, group_by)
    while cursor <= date_to:
        periods.append(cursor)
        cursor = _next_period(cursor, group_by)
    session_trend = [{'period': day.isoformat(), 'sessions': bucket_counts.get(day, 0)} for day in periods]

    staff_counts = list(sessions.values('staff_id').annotate(total=Count('pk'), approved=Count('pk', filter=Q(status='approved')),
                                                          rejected=Count('pk', filter=Q(status='rejected'))).order_by('staff_id'))
    staff_users = {u.pk: u for u in User.objects.filter(organization_id=request.tenant.pk, role__in=['admin', 'supervisor', 'staff'], pk__in=[r['staff_id'] for r in staff_counts]).filter(_same_practice_q(request.user))}
    staff = {}
    for row in staff_counts:
        user = staff_users.get(row['staff_id'])
        key = user.pk if user else None
        entry = staff.setdefault(key, {'staff_id': key, 'name': (f'{user.first_name} {user.last_name}'.strip() or 'Staff member') if user else 'Unassigned / unavailable', 'sessions': 0, 'approved': 0, 'rejected': 0})
        entry['sessions'] += row['total']
        entry['approved'] += row['approved']
        entry['rejected'] += row['rejected']
    for entry in staff.values():
        entry['approval_rate'] = _percent(entry['approved'], entry['approved'] + entry['rejected'])

    targets = Target.objects.filter(program__organization_id=request.tenant.pk, program__external_client_id__in=ids).exclude(program__category='instructions_only')
    mastery = targets.aggregate(total_targets=Count('pk'), mastered_targets=Count('pk', filter=Q(status='mastered')))
    mastery['current_mastery_rate'] = _percent(mastery['mastered_targets'], mastery['total_targets'])
    changes = TargetStatusChange.objects.filter(target_id__in=targets.values('pk'), to_status='mastered', created_at__gte=start, created_at__lt=end).exclude(from_status='mastered')
    mastery['targets_mastered_in_period'] = changes.values('target_id').distinct().count()
    # Resolve aliases to the visible client cohort without counting multiple aliases twice.
    mastered_aliases = set(changes.values_list('target__program__external_client_id', flat=True))
    mastery['clients_with_mastery_in_period'] = sum(c['id'] in mastered_aliases or (c['external_id'].isdigit() and int(c['external_id']) in mastered_aliases) for c in clients)

    history = defaultdict(list)
    for observation in ClientStatusObservation.objects.filter(client_id__in=[c['id'] for c in clients], observed_at__lt=end).order_by('observed_at', 'id').values('client_id', 'status', 'observed_at'):
        history[observation['client_id']].append(observation)
    trend = []
    for day in periods:
        cutoff = min(datetime.combine(min(_next_period(day, group_by), date_to + timedelta(days=1)), time.min, zone), now + timedelta(microseconds=1))
        active = inactive = unknown = 0
        for client in clients:
            if client['created_at'] >= cutoff:
                continue
            observed = next((h for h in reversed(history[client['id']]) if h['observed_at'] < cutoff), None)
            if observed is None:
                unknown += 1
            elif observed['status'] == 'active':
                active += 1
            else:
                inactive += 1
        trend.append({'period': day.isoformat(), 'active': active if not unknown else None,
                      'inactive': inactive if not unknown else None, 'unknown_clients': unknown})

    return {
        'date_from': date_from, 'date_to': date_to, 'group_by': group_by, 'timezone': str(zone), 'generated_at': now,
        'scope': 'Current authorized client cohort; practice restrictions apply.', 'ambiguous_identifiers_excluded': ambiguous,
        'sessions': counts, 'session_trend': session_trend,
        'staff': sorted(staff.values(), key=lambda s: (-s['sessions'], s['name'])),
        'pending_review': {'sessions': _aging(SessionRun.objects.filter(external_client_id__in=ids, status='submitted'), now),
                           'notes': _aging(LessonNote.objects.filter(external_client_id__in=ids, status='submitted'), now)},
        'mastery': mastery,
        'clients': {'total': len(clients), 'active': sum(c['status'] == 'active' for c in clients),
                    'inactive': sum(c['status'] != 'active' for c in clients)},
        'client_status_trend': trend,
        'status_tracking_started_at': min((h['observed_at'] for rows in history.values() for h in rows), default=None),
    }
