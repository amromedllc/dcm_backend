from ninja import NinjaAPI
from apps.accounts.api import router as accounts_router
from apps.accounts.auth import jwt_auth, api_key_auth
from apps.clients.api import router as clients_router
from apps.programs.api import router as programs_router
from apps.sessions.api import router as sessions_router
from apps.notes.api import router as notes_router
from apps.analytics.api import router as analytics_router
from apps.exports.api import router as exports_router
from apps.notifications.api import router as notifications_router
from apps.integrations.api import router as integrations_router
from apps.audit.api import router as audit_router

api = NinjaAPI(
    title='DCM Platform API',
    version='1.0.0',
    description=(
        'Data Collection Platform API. '
        'Authenticate with Bearer JWT (users) or X-API-Key header (facility integrations).'
    ),
    docs_url='/docs',
    auth=[jwt_auth, api_key_auth],
)

api.add_router('/auth', accounts_router, tags=['Authentication'])
api.add_router('/clients', clients_router, tags=['Clients'])
api.add_router('/', programs_router, tags=['Programs'])
api.add_router('/', sessions_router, tags=['Sessions'])
api.add_router('/', notes_router, tags=['Notes'])
api.add_router('/', analytics_router, tags=['Analytics'])
api.add_router('/', exports_router, tags=['Exports'])
api.add_router('/', notifications_router, tags=['Notifications'])
api.add_router('/integrations', integrations_router, tags=['Integrations'])
api.add_router('/', audit_router, tags=['Audit'])


@api.get('/dashboard', auth=jwt_auth, tags=['System'])
def dashboard(request):
    """
    Single endpoint that returns everything the dashboard needs in one round-trip:
    - pending review counts (sessions + notes)
    - today's appointments
    - active client count
    - unread notification count
    - recent audit activity (admin only)
    """
    from django.utils import timezone
    from django.db.models import Count, Q
    from apps.sessions.models import SessionRun
    from apps.notes.models import LessonNote
    from apps.notifications.models import Notification
    from apps.clients.models import Client

    user = request.user
    today = timezone.now().date()

    sessions_pending = SessionRun.objects.filter(status='submitted').count()

    my_open_sessions = (
        SessionRun.objects.filter(staff_id=user.id, status='open').count()
        if user.role == 'staff' else None
    )

    notes_pending = LessonNote.objects.filter(status='submitted').count()

    my_draft_notes = (
        LessonNote.objects.filter(staff_id=user.id, status='draft').count()
        if user.role == 'staff' else None
    )

    active_clients = Client.objects.filter(status='active').count()

    # Total clients (all statuses) — the admin dashboard's "Total clients" card
    total_clients = Client.objects.count()

    unread_notifications = Notification.objects.filter(
        recipient_id=user.id, read_at__isnull=True
    ).count()

    sessions_today = SessionRun.objects.filter(started_at__date=today).count()

    result = {
        'sessions_pending_review': sessions_pending,
        'notes_pending_review': notes_pending,
        'active_clients': active_clients,
        'total_clients': total_clients,
        'unread_notifications': unread_notifications,
        'sessions_today': sessions_today,
        'my_open_sessions': my_open_sessions,
        'my_draft_notes': my_draft_notes,
    }

    # Recent audit activity, daily activity trend, staff productivity — admin/supervisor only
    if user.has_role('admin', 'supervisor'):
        from datetime import timedelta
        from django.db.models.functions import TruncDate
        from apps.audit.models import AuditLog
        from apps.accounts.models import User

        recent = AuditLog.objects.select_related()[:10]
        result['recent_activity'] = [
            {
                'actor_email': log.actor_email,
                'action': log.action,
                'model': log.model,
                'object_repr': log.object_repr,
                'timestamp': log.timestamp,
            }
            for log in recent
        ]

        # 14-day activity trend — sessions/notes submitted vs. approved, by day
        trend_start = today - timedelta(days=13)

        def _counts_by_day(qs, date_field):
            return dict(
                qs.annotate(day=TruncDate(date_field)).values('day')
                .annotate(c=Count('id')).values_list('day', 'c')
            )

        sessions_submitted_by_day = _counts_by_day(
            SessionRun.objects.filter(submitted_at__date__gte=trend_start), 'submitted_at',
        )
        sessions_approved_by_day = _counts_by_day(
            SessionRun.objects.filter(status='approved', reviewed_at__date__gte=trend_start), 'reviewed_at',
        )
        notes_submitted_by_day = _counts_by_day(
            LessonNote.objects.filter(submitted_at__date__gte=trend_start), 'submitted_at',
        )
        notes_approved_by_day = _counts_by_day(
            LessonNote.objects.filter(status='approved', approved_at__date__gte=trend_start), 'approved_at',
        )

        daily_trend = []
        for i in range(14):
            day = trend_start + timedelta(days=i)
            daily_trend.append({
                'date': day.isoformat(),
                'sessions_submitted': sessions_submitted_by_day.get(day, 0),
                'sessions_approved': sessions_approved_by_day.get(day, 0),
                'notes_submitted': notes_submitted_by_day.get(day, 0),
                'notes_approved': notes_approved_by_day.get(day, 0),
            })
        result['daily_trend'] = daily_trend

        # Staff productivity — sessions/notes recorded in the last 30 days.
        # distinct=True on both Counts avoids the join fan-out that would
        # otherwise inflate counts (session_runs and authored_notes are two
        # separate reverse FKs joined onto the same User row).
        productivity_start = today - timedelta(days=29)
        staff_rows = (
            User.objects.filter(role='staff', is_active=True)
            .annotate(
                sessions_count=Count(
                    'session_runs',
                    filter=Q(session_runs__started_at__date__gte=productivity_start),
                    distinct=True,
                ),
                notes_count=Count(
                    'authored_notes',
                    filter=Q(authored_notes__note_date__gte=productivity_start),
                    distinct=True,
                ),
            )
            .order_by('-sessions_count', 'first_name')[:10]
        )
        result['staff_productivity'] = [
            {
                'staff_id': u.id,
                'staff_name': f'{u.first_name} {u.last_name}'.strip() or u.email,
                'sessions_count': u.sessions_count,
                'notes_count': u.notes_count,
            }
            for u in staff_rows
        ]

    return result
