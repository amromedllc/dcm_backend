from datetime import date, timedelta
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import caregiver_auth
from apps.analytics.services import get_client_progress_report
from apps.clients.models import Client
from apps.integrations.tpms_auth_client import (
    TpmsAuthError,
    list_client_portal_appointments,
    get_tpms_access_token,
    clear_tpms_access_token,
)
from apps.notes.models import LessonNote
from apps.sessions.models import SessionRun, TrialEvent, BehaviorEvent
from apps.sessions.schemas import SessionRunSchema, TrialEventSchema, BehaviorEventSchema
from apps.sessions.api import _serialize_session
from .schemas import (
    CaregiverAppointmentSchema, CaregiverSessionRefSchema,
    CaregiverNoteListItem, CaregiverNoteDetail,
)

router = Router(auth=caregiver_auth)

_DEFAULT_SCHEDULE_DAYS = 30
_MAX_REPORT_RANGE_DAYS = 366


def _scope(request) -> tuple[int, Client]:
    """The one client this caregiver may ever see. Raises rather than
    returning a nullable value a caller might forget to check."""
    external_client_id = request.user.external_client_id
    if external_client_id is None:
        raise HttpError(403, 'Portal account is not linked to a client')
    client = Client.objects.filter(external_id=str(external_client_id)).order_by('id').first()
    if client is None:
        raise HttpError(403, 'Your portal account is not set up in DCM yet. Please contact your provider.')
    tenant = getattr(request, 'tenant', None)
    if (
        tenant is None
        or client.external_admin_id is None
        or not tenant.tpms_admin_ids.filter(admin_id=client.external_admin_id).exists()
    ):
        raise HttpError(403, 'Access denied')
    return external_client_id, client


def _split_hours(raw: str) -> tuple[str | None, str | None]:
    parts = raw.split(' to ', 1)
    if len(parts) == 2:
        return parts[0].strip() or None, parts[1].strip() or None
    return raw.strip() or None, None


def _project_appointment(row: dict) -> dict:

    def pick(*keys):
        for key in keys:
            value = row.get(key)
            if value not in (None, ''):
                return str(value)
        return None

    start_time = pick('start_time', 'from_time', 'time_from')
    end_time = pick('end_time', 'to_time', 'time_to')
    if start_time is None and end_time is None:
        hours = pick('hours')
        if hours:
            start_time, end_time = _split_hours(hours)

    return {
        'id': pick('id', 'appointment_id', 'session_id'),
        'date': pick('date', 'appointment_date', 'session_date', 'scheduled_date'),
        'start_time': start_time,
        'end_time': end_time,
        'provider_name': pick('provider_name', 'therapist_name', 'staff_name', 'provider'),
        'status': pick('status', 'appointment_status'),
        'service_type': pick('service_type', 'session_type', 'program', 'service_name'),
        'location': pick('location', 'place_of_service', 'pos'),
        'notes': pick('notes', 'description'),
    }


@router.get('/schedule', response=list[CaregiverAppointmentSchema])
def portal_schedule(request, start_date: date | None = None, end_date: date | None = None):
    """Live proxy to TPMS /client-portal/appointment for this caregiver's own client."""
    _scope(request)

    frm = start_date or date.today()
    to = end_date or (frm + timedelta(days=_DEFAULT_SCHEDULE_DAYS))
    if frm > to:
        raise HttpError(400, 'start_date must be before end_date')

    token = get_tpms_access_token(request.user.id)
    if not token:
        raise HttpError(401, 'TherapyPMS session expired. Please log in again.')

    try:
        rows = list_client_portal_appointments(
            token,
            start_date=frm.strftime('%m/%d/%Y'),
            end_date=to.strftime('%m/%d/%Y'),
        )
    except TpmsAuthError as exc:
        status = getattr(exc, 'status_code', None)
        if status in (401, 403):
            clear_tpms_access_token(request.user.id)
            raise HttpError(401, 'TherapyPMS session expired. Please log in again.') from exc
        raise HttpError(502, str(exc) or 'Failed to load appointments from TherapyPMS') from exc

    return [_project_appointment(row) for row in rows]


# ---------------------------------------------------------------------------
# Session (watch a session staff/supervisor is recording, live)
# ---------------------------------------------------------------------------

def _get_caregiver_session_or_404(request, session_id: int) -> SessionRun:
    external_client_id, _ = _scope(request)
    try:
        return SessionRun.objects.select_related('staff').get(
            id=session_id, external_client_id=external_client_id,
        )
    except SessionRun.DoesNotExist:
        raise HttpError(404, 'Session not found')


@router.get('/sessions/resolve', response=CaregiverSessionRefSchema)
def resolve_session_for_appointment(request, appointment_id: str, appointment_date: date | None = None):
    external_client_id, _ = _scope(request)
    print("Resolving session for appointment_id:", appointment_id, "and appointment_date:", appointment_date)

    session = None
    try:
        appt_id_int = int(appointment_id)
    except (TypeError, ValueError):
        appt_id_int = None

    if appt_id_int is not None:
        session = (
            SessionRun.objects
            .filter(external_client_id=external_client_id, external_appointment_id=appt_id_int)
            .order_by('-started_at')
            .first()
        )

    if session is None and appointment_date is not None:
        session = (
            SessionRun.objects
            .filter(external_client_id=external_client_id, started_at__date=appointment_date)
            .order_by('-started_at')
            .first()
        )

    if session is None:
        session = (
            SessionRun.objects
            .filter(external_client_id=external_client_id, status='open')
            .order_by('-started_at')
            .first()
        )

    if session is None:
        raise HttpError(404, 'No session has been recorded for this appointment yet')

    return {'session_id': session.id, 'status': session.status}


@router.get('/sessions/{session_id}', response=SessionRunSchema)
def portal_session(request, session_id: int):
    session = _get_caregiver_session_or_404(request, session_id)
    return _serialize_session(session)


@router.get('/sessions/{session_id}/trials', response=list[TrialEventSchema])
def portal_session_trials(request, session_id: int):
    _get_caregiver_session_or_404(request, session_id)
    return list(TrialEvent.objects.filter(session_run_id=session_id).order_by('recorded_at'))


@router.get('/sessions/{session_id}/behaviors', response=list[BehaviorEventSchema])
def portal_session_behaviors(request, session_id: int):
    _get_caregiver_session_or_404(request, session_id)
    return list(BehaviorEvent.objects.filter(session_run_id=session_id).order_by('occurred_at'))


@router.get('/progress')
def portal_progress(request, date_from: date | None = None, date_to: date | None = None):
    """This caregiver's own client's progress report — reuses the same
    service function the staff-facing report page calls, unscoped by any
    permission key (identity scoping via _scope() is the only gate)."""
    external_client_id, _ = _scope(request)

    to = date_to or date.today()
    frm = date_from or (to - timedelta(days=90))
    if (to - frm).days > _MAX_REPORT_RANGE_DAYS:
        raise HttpError(400, f'Date range cannot exceed {_MAX_REPORT_RANGE_DAYS} days')
    if frm > to:
        raise HttpError(400, 'date_from must be before date_to')

    return get_client_progress_report(external_client_id, frm, to)


@router.get('/notes', response=list[CaregiverNoteListItem])
def portal_notes(request):
    """Approved notes flagged as requiring caregiver visibility only —
    stricter than the staff sign_note endpoint's 'any non-draft', since a
    rejected note is an in-progress internal correction, not family-facing."""
    external_client_id, _ = _scope(request)
    qs = (
        LessonNote.objects
        .filter(
            external_client_id=external_client_id,
            requires_caregiver_signature=True,
            status=LessonNote.Status.APPROVED,
        )
        .select_related('template')
        .order_by('-note_date')
    )
    return [
        {
            'id': note.id,
            'note_date': note.note_date,
            'template_name': note.template.name if note.template else None,
            'status': note.status,
            'created_at': note.created_at,
        }
        for note in qs
    ]


@router.get('/notes/{note_id}', response=CaregiverNoteDetail)
def portal_note(request, note_id: int):
    external_client_id, _ = _scope(request)
    note = (
        LessonNote.objects
        .filter(
            id=note_id,
            external_client_id=external_client_id,
            requires_caregiver_signature=True,
            status=LessonNote.Status.APPROVED,
        )
        .select_related('template')
        .first()
    )
    if note is None:
        raise HttpError(404, 'Note not found')
    return {
        'id': note.id,
        'note_date': note.note_date,
        'template_name': note.template.name if note.template else None,
        'status': note.status,
        'body': note.body,
        'created_at': note.created_at,
        'updated_at': note.updated_at,
    }
