from datetime import date
from django.db.models import Count
from django.utils import timezone
from ninja import Router, Form, File
from ninja.files import UploadedFile
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from apps.accounts.permissions import require_permission
from .models import Appointment, SessionRun, TrialEvent, BehaviorEvent, ABCEvent, SessionMedia, SessionMediaComment
from .schemas import (
    AppointmentSchema, AppointmentCreateRequest, AppointmentUpdateRequest,
    AssignProgramsRequest, AssignedProgramSchema,
    SessionRunSchema, SessionStartRequest, SessionSubmitRequest, SessionRejectRequest,
    SessionLinkAppointmentRequest,
    SessionSubmitResponse, TargetAdvancedSchema, TargetFadedSchema,
    TrialEventSchema, TrialEventCreateRequest,
    BehaviorEventSchema, BehaviorEventCreateRequest,
    ABCEventSchema, ABCEventCreateRequest,
    SessionSyncPayload, SessionSyncResult,
    TrialSummaryItem,
    SessionMediaSchema, SessionMediaUpdateRequest,
    SessionMediaCommentSchema, SessionMediaCommentCreateRequest,
)
from .services import build_program_snapshot, submit_session, approve_session, reject_session

router = Router(auth=jwt_auth)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _accessible_external_client_ids(request) -> set[int]:
    """
    Practice/staff-assignment boundary translated into the loose
    external_client_id convention Appointment/SessionRun store (either a TPMS
    patient id, or for native appointments the local Client.id — by
    convention, see project memory on native mode). Reuses
    _get_accessible_clients so appointments/sessions are bound by the exact
    same practice/assignment rule the client list already enforces, instead
    of only checking role/staff-ownership — otherwise, in a tenant schema
    serving more than one TPMS practice, any staff/admin/supervisor from one
    practice could read or act on another practice's appointments/sessions
    purely by id.
    """
    from apps.clients.api import _get_accessible_clients

    ids: set[int] = set()
    for client_id, external_id in _get_accessible_clients(request).values_list('id', 'external_id'):
        ids.add(client_id)
        if external_id:
            try:
                ids.add(int(external_id))
            except (TypeError, ValueError):
                pass
    return ids


def _get_session_or_404(session_id: int, request) -> SessionRun:
    """
    Staff may only reach their own sessions (matches the scoping already
    applied in list_sessions below) — admin/supervisor can reach any session
    for a client in their accessible-clients scope. Without this, any staff
    user could read, edit, or delete another staff member's session (and its
    trial/behavior/ABC data) purely by guessing/incrementing a session id,
    since every caller of this helper — get/delete session, trials,
    behaviors, abc, submit, sync — otherwise had no ownership check at all.
    """
    qs = SessionRun.objects.select_related('staff').filter(
        external_client_id__in=_accessible_external_client_ids(request),
    )
    if request.user.role == 'staff':
        qs = qs.filter(staff_id=request.user.id)
    try:
        return qs.get(id=session_id)
    except SessionRun.DoesNotExist:
        raise HttpError(404, 'Session not found')


def _build_trial_summary(session_run: SessionRun) -> list[TrialSummaryItem]:
    """
    Per-target trial summary for a single session. Scoped to one session_run,
    so 1 grouped query + one score-breakdown query — fine for single-session
    endpoints. list_sessions has its own batched version (_trial_summaries_for_sessions)
    to avoid repeating this per session in a list.
    """
    rows = (
        session_run.trial_events
        .values('target_id', 'target_name', 'response_score')
        .annotate(count=Count('id'))
    )
    by_target: dict[int, dict] = {}
    for row in rows:
        t = by_target.setdefault(row['target_id'], {'target_name': row['target_name'], 'total': 0, 'score_counts': {}})
        t['total'] += row['count']
        t['score_counts'][row['response_score']] = t['score_counts'].get(row['response_score'], 0) + row['count']

    result = []
    for target_id, t in by_target.items():
        max_score = _max_score_for_target(session_run.program_snapshot, target_id)
        correct = t['score_counts'].get(max_score, 0) if max_score is not None else 0
        total = t['total']
        result.append(TrialSummaryItem(
            target_id=target_id,
            target_name=t['target_name'],
            total_trials=total,
            correct_count=correct,
            pct_correct=round((correct / total * 100), 1) if total else 0.0,
        ))
    return result


def _trial_summaries_for_sessions(sessions: list[SessionRun]) -> dict[int, list[TrialSummaryItem]]:
    """
    Batched equivalent of _build_trial_summary for a list of sessions — one
    query total instead of ~(1 + 2 * distinct targets) per session. Used by
    list_sessions, which previously ran _build_trial_summary per row and
    turned "load my sessions" into hundreds of queries once a staff member
    had any meaningful session history.
    """
    session_ids = [s.id for s in sessions]
    if not session_ids:
        return {}

    rows = (
        TrialEvent.objects
        .filter(session_run_id__in=session_ids)
        .values('session_run_id', 'target_id', 'target_name', 'response_score')
        .annotate(count=Count('id'))
    )
    by_session: dict[int, dict[int, dict]] = {}
    for row in rows:
        by_target = by_session.setdefault(row['session_run_id'], {})
        t = by_target.setdefault(row['target_id'], {'target_name': row['target_name'], 'total': 0, 'score_counts': {}})
        t['total'] += row['count']
        t['score_counts'][row['response_score']] = t['score_counts'].get(row['response_score'], 0) + row['count']

    result: dict[int, list[TrialSummaryItem]] = {}
    for session in sessions:
        by_target = by_session.get(session.id, {})
        items = []
        for target_id, t in by_target.items():
            max_score = _max_score_for_target(session.program_snapshot, target_id)
            correct = t['score_counts'].get(max_score, 0) if max_score is not None else 0
            total = t['total']
            items.append(TrialSummaryItem(
                target_id=target_id,
                target_name=t['target_name'],
                total_trials=total,
                correct_count=correct,
                pct_correct=round((correct / total * 100), 1) if total else 0.0,
            ))
        result[session.id] = items
    return result


def _max_score_for_target(snapshot: dict, target_id: int) -> int | None:
    """Finds the max response score from the prompting template captured in the snapshot."""
    for program in snapshot.get('programs', []):
        for target in program.get('targets', []):
            if target['id'] == target_id:
                pt = target.get('prompting_template')
                if pt and pt.get('levels'):
                    return max(level['score'] for level in pt['levels'])
    return None


def _get_tpms_appointment(external_appointment_id: int | None):
    if not external_appointment_id:
        return None
    return (
        Appointment.objects
        .filter(external_id=str(external_appointment_id))
        .only('start_time', 'end_time')
        .first()
    )


def _aware(dt):
    if dt is None:
        return None
    from django.utils import timezone
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _serialize_session(
    session: SessionRun,
    dcm_appt=None,
    trial_summary: list[TrialSummaryItem] | None = None,
    behavior_event_count: int | None = None,
    abc_event_count: int | None = None,
) -> dict:
    """
    trial_summary/behavior_event_count/abc_event_count can be precomputed and
    passed in (see list_sessions) to avoid per-session queries when serializing
    a batch. Single-session callers (get/create/submit/etc.) omit them and fall
    back to the per-session queries below.
    """
    staff = session.staff
    staff_name = f'{staff.first_name} {staff.last_name}'.strip() if staff else None
    if dcm_appt is None and session.external_appointment_id:
        dcm_appt = _get_tpms_appointment(session.external_appointment_id)
    return {
        'id': session.id,
        'client_id': session.external_client_id,
        'staff_id': session.staff_id,
        'staff_name': staff_name or (staff.email if staff else None),
        'appointment_id': session.external_appointment_id,
        'appointment_start_time': dcm_appt.start_time if dcm_appt else None,
        'appointment_end_time': dcm_appt.end_time if dcm_appt else None,
        'lesson_id': session.lesson_id,
        'status': session.status,
        'started_at': session.started_at,
        'ended_at': session.ended_at,
        'submitted_at': session.submitted_at,
        'reviewed_at': session.reviewed_at,
        'rejection_reason': session.rejection_reason,
        'program_snapshot': session.program_snapshot,
        'trial_summary': trial_summary if trial_summary is not None else _build_trial_summary(session),
        'behavior_event_count': behavior_event_count if behavior_event_count is not None else session.behavior_events.count(),
        'abc_event_count': abc_event_count if abc_event_count is not None else session.abc_events.count(),
        'created_at': session.created_at,
    }


# ---------------------------------------------------------------------------
# TPMS appointments via iOS API (TherapyPMS DB removed)
# ---------------------------------------------------------------------------

def _tpms_status(raw: str | None) -> str:
    """Map TPMS appointment status string to DCM status."""
    s = (raw or '').lower()
    if s in ('rendered', 'completed', 'kept'):
        return 'completed'
    if s in ('cancelled', 'canceled'):
        return 'cancelled'
    if s in ('no show', 'no-show', 'noshow'):
        return 'no_show'
    return 'scheduled'


def _find_appointment(appt_id: int) -> Appointment | None:
    """Prefer external_id match (TPMS session id), then local PK."""
    return (
        Appointment.objects
        .filter(external_id=str(appt_id))
        .select_related('lesson')
        .first()
    ) or (
        Appointment.objects
        .filter(id=appt_id)
        .select_related('lesson')
        .first()
    )


@router.get('/provider-appointments', response=list[AppointmentSchema])
def list_provider_appointments(
    request,
    external_employee_id: int,
    status: str | None = None,
):
    """Return appointments for a provider via the TherapyPMS iOS API."""
    from apps.clients.models import Client
    from apps.integrations.tpms_auth_client import (
        TpmsAuthError,
        clear_tpms_access_token,
        get_tpms_access_token,
        list_recurring_appointments,
    )
    from apps.clients.api import _serialize_tpms_api_appointments

    token = get_tpms_access_token(request.user.id)
    if not token:
        raise HttpError(401, 'TherapyPMS session expired. Please log in again.')

    patient_ids: list[int] = []
    qs = Client.objects.exclude(external_id='').exclude(external_id__isnull=True)
    if request.user.external_admin_id is not None:
        qs = qs.filter(external_admin_id=request.user.external_admin_id)
    for ext in qs.values_list('external_id', flat=True):
        try:
            patient_ids.append(int(ext))
        except (TypeError, ValueError):
            continue

    if not patient_ids:
        return []

    try:
        appointments = list_recurring_appointments(
            token,
            patient_ids=patient_ids,
            provider_ids=[int(external_employee_id)],
        )
    except TpmsAuthError as exc:
        if exc.status_code in {401, 403}:
            clear_tpms_access_token(request.user.id)
            raise HttpError(401, 'TherapyPMS session expired. Please log in again.') from exc
        raise HttpError(502, str(exc) or 'Failed to load appointments from TherapyPMS') from exc

    # Use first matching DCM client id as a placeholder; serializer remaps per row via external_id
    dcm_client_id = 0
    return _serialize_tpms_api_appointments(
        appointments=appointments,
        dcm_client_id=dcm_client_id,
        status=status,
        from_date=None,
        to_date=None,
    )


@router.get('/my-schedule', response=list[AppointmentSchema])
def my_schedule(request, date: str | None = None):
    """
    Return appointments for the logged-in staff member on a given date
    (defaults to today). Uses TherapyPMS iOS API for TPMS-linked users.
    """
    from datetime import date as dt_date
    from apps.clients.models import Client
    from apps.integrations.tpms_auth_client import (
        TpmsAuthError,
        clear_tpms_access_token,
        get_tpms_access_token,
        list_recurring_appointments,
    )
    from apps.clients.api import _serialize_tpms_api_appointments

    target_date = date or dt_date.today().isoformat()
    try:
        target = dt_date.fromisoformat(target_date)
    except ValueError:
        raise HttpError(400, 'Invalid date — use YYYY-MM-DD')

    employee_id = request.user.external_employee_id
    if employee_id is None:
        return list(
            _appt_qs()
            .filter(staff_id=request.user.id, start_time__date=target)
            .order_by('start_time')
        )

    token = get_tpms_access_token(request.user.id)
    if not token:
        raise HttpError(401, 'TherapyPMS session expired. Please log in again.')

    patient_ids: list[int] = []
    qs = Client.objects.exclude(external_id='').exclude(external_id__isnull=True)
    if request.user.external_admin_id is not None:
        qs = qs.filter(external_admin_id=request.user.external_admin_id)
    for ext in qs.values_list('external_id', flat=True):
        try:
            patient_ids.append(int(ext))
        except (TypeError, ValueError):
            continue

    if not patient_ids:
        return []

    try:
        appointments = list_recurring_appointments(
            token,
            patient_ids=patient_ids,
            provider_ids=[int(employee_id)],
        )
    except TpmsAuthError as exc:
        if exc.status_code in {401, 403}:
            clear_tpms_access_token(request.user.id)
            raise HttpError(401, 'TherapyPMS session expired. Please log in again.') from exc
        raise HttpError(502, str(exc) or 'Failed to load appointments from TherapyPMS') from exc

    return _serialize_tpms_api_appointments(
        appointments=appointments,
        dcm_client_id=0,
        status=None,
        from_date=target,
        to_date=target,
    )


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

def _appt_qs():
    return Appointment.objects.annotate(
        assigned_program_count=Count('lesson__lesson_programs', distinct=True)
    )


def _get_appointment_or_404(request, appt_id: int) -> Appointment:
    """
    Same staff-ownership + accessible-client rule list_appointments already
    applies — staff may only reach their own appointments, and every role is
    bound to the accessible-clients practice/assignment scope. Without this,
    any staff user could read another staff member's appointment (client,
    notes, schedule) purely by guessing/incrementing an id.
    """
    qs = _appt_qs().filter(external_client_id__in=_accessible_external_client_ids(request))
    if request.user.role == 'staff':
        qs = qs.filter(staff_id=request.user.id)
    try:
        return qs.get(id=appt_id)
    except Appointment.DoesNotExist:
        raise HttpError(404, 'Appointment not found')


@router.get('/appointments', response=list[AppointmentSchema])
def list_appointments(
    request,
    client_id: int | None = None,
    staff_id: int | None = None,
    date: str | None = None,
    status: str | None = None,
):
    qs = _appt_qs().filter(external_client_id__in=_accessible_external_client_ids(request))
    if client_id:
        qs = qs.filter(external_client_id=client_id)
    if staff_id:
        qs = qs.filter(staff_id=staff_id)
    if date:
        qs = qs.filter(start_time__date=date)
    if status:
        qs = qs.filter(status=status)
    if request.user.role == 'staff':
        qs = qs.filter(staff_id=request.user.id)
    return list(qs.select_related())


@router.post('/appointments', response={201: AppointmentSchema})
def create_appointment(request, data: AppointmentCreateRequest):
    require_permission(request, 'appointments_create')
    if data.client_id not in _accessible_external_client_ids(request):
        raise HttpError(404, 'Client not found')
    payload = data.dict()
    external_client_id = payload.pop('client_id', None)
    appt = Appointment.objects.create(created_by=request.user, external_client_id=external_client_id, **payload)
    return 201, appt


@router.get('/appointments/{appt_id}', response=AppointmentSchema)
def get_appointment(request, appt_id: int):
    return _get_appointment_or_404(request, appt_id)


@router.get('/appointments/{appt_id}/programs', response=list[AssignedProgramSchema])
def get_appointment_programs(request, appt_id: int):
    """Returns programs currently assigned to this appointment."""
    appt = _find_appointment(appt_id)
    accessible_ids = _accessible_external_client_ids(request)
    if not appt or appt.external_client_id not in accessible_ids or (
        request.user.role == 'staff' and appt.staff_id != request.user.id
    ):
        return []
    if not appt.lesson_id:
        return []
    from apps.programs.models import LessonProgram
    rows = (
        LessonProgram.objects
        .filter(lesson_id=appt.lesson_id)
        .select_related('program')
        .order_by('display_order')
    )
    return [
        AssignedProgramSchema(
            id=lp.program.id,
            name=lp.program.name,
            category=lp.program.category,
            target_count=lp.program.targets.filter(status='active').count(),
        )
        for lp in rows
    ]


@router.post('/appointments/{appt_id}/programs', response=AppointmentSchema)
def assign_appointment_programs(request, appt_id: int, data: AssignProgramsRequest):
    """
    Supervisor assigns which programs to run in this appointment.
    Creates or reuses a Lesson linked to the appointment, then replaces its program list.

    appt_id may be a DCM internal id or a TPMS external id — both are handled.
    If no DCM Appointment row exists yet, one is created from the times supplied
    by the client (from the live TherapyPMS API list) — the TPMS DB is not used.
    """
    require_permission(request, 'appointments_edit')

    accessible_ids = _accessible_external_client_ids(request)
    appt = _find_appointment(appt_id)
    if appt and appt.external_client_id not in accessible_ids:
        raise HttpError(404, 'Appointment not found')

    if not appt:
        if not data.client_id:
            raise HttpError(400, 'client_id is required to assign programs to a new appointment')
        if data.client_id not in accessible_ids:
            raise HttpError(404, 'Client not found')
        if not data.start_time:
            raise HttpError(
                400,
                'start_time is required to assign programs to a TherapyPMS appointment '
                'that has not been linked in DCM yet',
            )
        end = data.end_time or data.start_time
        appt = Appointment.objects.create(
            external_id=str(appt_id),
            external_client_id=data.client_id,
            source=Appointment.Source.SYNCED,
            start_time=data.start_time,
            end_time=end,
            service_type=data.service_type or '',
            status=Appointment.Status.SCHEDULED,
            created_by=request.user,
        )

    from apps.programs.models import Lesson, LessonProgram
    from django.db import transaction

    with transaction.atomic():
        if appt.lesson_id:
            lesson = appt.lesson
        else:
            lesson = Lesson.objects.create(
                external_client_id=data.client_id or appt.external_client_id,
                name=appt.start_time.strftime('Session %b %d, %Y'),
                created_by=request.user,
            )
            appt.lesson_id = lesson.id
            appt.save(update_fields=['lesson_id'])

        LessonProgram.objects.filter(lesson=lesson).delete()
        for order, prog_id in enumerate(data.program_ids):
            LessonProgram.objects.create(
                lesson=lesson,
                program_id=prog_id,
                display_order=order,
            )

    return _appt_qs().get(id=appt.id)


@router.patch('/appointments/{appt_id}', response=AppointmentSchema)
def update_appointment(request, appt_id: int, data: AppointmentUpdateRequest):
    require_permission(request, 'appointments_edit')
    try:
        appt = Appointment.objects.get(
            id=appt_id,
            external_client_id__in=_accessible_external_client_ids(request),
        )
    except Appointment.DoesNotExist:
        raise HttpError(404, 'Appointment not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(appt, field, value)
    appt.save()
    return appt


# ---------------------------------------------------------------------------
# Sessions — start / list / detail
# ---------------------------------------------------------------------------

@router.post('/sessions', response={201: SessionRunSchema})
def start_session(request, data: SessionStartRequest):
    """
    Creates a new SessionRun and immediately captures the program snapshot.
    client_id is the TPMS client (patient) ID; appointment_id is the TPMS appointment ID.
    """
    if data.client_id not in _accessible_external_client_ids(request):
        raise HttpError(404, 'Client not found')
    lesson_id = data.lesson_id
    if not lesson_id and data.appointment_id:
        lesson_id = Appointment.objects.filter(id=data.appointment_id).values_list('lesson_id', flat=True).first()
    snapshot = build_program_snapshot(
        client_id=data.client_id,
        lesson_id=lesson_id,
        restrict_to_lesson=bool(data.appointment_id),
    )
    session = SessionRun.objects.create(
        external_client_id=data.client_id,
        staff=request.user,
        external_appointment_id=data.appointment_id,
        lesson_id=lesson_id,
        program_snapshot=snapshot,
        created_by=request.user,
    )
    return 201, _serialize_session(session)


@router.get('/sessions', response=list[SessionRunSchema])
def list_sessions(
    request,
    client_id: int | None = None,
    status: str | None = None,
    staff_id: int | None = None,
):
    qs = SessionRun.objects.select_related('staff').filter(
        external_client_id__in=_accessible_external_client_ids(request),
    )
    if client_id:
        qs = qs.filter(external_client_id=client_id)
    if status:
        qs = qs.filter(status=status)
    if request.user.role == 'staff':
        qs = qs.filter(staff_id=request.user.id)
    elif staff_id:
        qs = qs.filter(staff_id=staff_id)
    sessions = list(qs)
    appt_ids = [s.external_appointment_id for s in sessions if s.external_appointment_id]
    dcm_appts: dict[int, Appointment] = {}
    if appt_ids:
        for a in Appointment.objects.filter(external_id__in=[str(i) for i in appt_ids]).only(
            'external_id', 'start_time', 'end_time',
        ):
            try:
                dcm_appts[int(a.external_id)] = a
            except (TypeError, ValueError):
                continue

    # Batched trial/behavior/abc counts instead of computing them per-row —
    # see _trial_summaries_for_sessions for why that mattered.
    session_ids = [s.id for s in sessions]
    trial_summaries = _trial_summaries_for_sessions(sessions)
    behavior_counts = dict(
        BehaviorEvent.objects.filter(session_run_id__in=session_ids)
        .values('session_run_id').annotate(c=Count('id')).values_list('session_run_id', 'c')
    )
    abc_counts = dict(
        ABCEvent.objects.filter(session_run_id__in=session_ids)
        .values('session_run_id').annotate(c=Count('id')).values_list('session_run_id', 'c')
    )

    return [
        _serialize_session(
            s,
            dcm_appts.get(s.external_appointment_id),
            trial_summary=trial_summaries.get(s.id, []),
            behavior_event_count=behavior_counts.get(s.id, 0),
            abc_event_count=abc_counts.get(s.id, 0),
        )
        for s in sessions
    ]


@router.get('/sessions/{session_id}', response=SessionRunSchema)
def get_session(request, session_id: int):
    session = _get_session_or_404(session_id, request)
    return _serialize_session(session)


@router.post('/sessions/{session_id}/link-appointment', response=SessionRunSchema)
def link_session_appointment(request, session_id: int, data: SessionLinkAppointmentRequest):
    """
    Supervisor links a walk-in recording (no appointment_id) to an appointment
    after the fact — e.g. staff started the session ad-hoc instead of from
    the schedule, or the appointment wasn't in the schedule yet when they
    recorded. Web-only; moves the session out of the client's "Walk-in
    Recordings" bucket and under that appointment's date group instead.
    """
    require_permission(request, 'appointments_edit')

    session = _get_session_or_404(session_id, request)
    appt = _find_appointment(data.appointment_id)
    if not appt:
        raise HttpError(404, 'Appointment not found')
    if appt.external_client_id != session.external_client_id:
        raise HttpError(400, "That appointment belongs to a different client than this session")

    session.external_appointment_id = data.appointment_id
    session.save(update_fields=['external_appointment_id'])
    return _serialize_session(session)


@router.delete('/sessions/{session_id}', response={204: None})
def delete_session(request, session_id: int):
    """Discard an open session that has no recorded data yet."""
    session = _get_session_or_404(session_id, request)
    if session.status != SessionRun.Status.OPEN:
        raise HttpError(409, 'Only open sessions can be deleted')
    session.delete()
    return 204, None


# ---------------------------------------------------------------------------
# Trial events
# ---------------------------------------------------------------------------

@router.get('/sessions/{session_id}/trials', response=list[TrialEventSchema])
def list_trials(request, session_id: int):
    _get_session_or_404(session_id, request)
    return list(TrialEvent.objects.filter(session_run_id=session_id))


@router.post('/sessions/{session_id}/trials', response={201: TrialEventSchema})
def add_trial(request, session_id: int, data: TrialEventCreateRequest):
    session = _get_session_or_404(session_id, request)
    if not session.is_editable:
        raise HttpError(409, f'Session is {session.status} — cannot add trials')
    trial = TrialEvent.objects.create(session_run_id=session_id, **data.dict())
    return 201, trial


@router.delete('/sessions/{session_id}/trials/{trial_id}', response={204: None})
def delete_trial(request, session_id: int, trial_id: int):
    session = _get_session_or_404(session_id, request)
    if not session.is_editable:
        raise HttpError(409, f'Session is {session.status} — cannot delete trials')
    try:
        TrialEvent.objects.get(id=trial_id, session_run_id=session_id).delete()
    except TrialEvent.DoesNotExist:
        raise HttpError(404, 'Trial not found')
    return 204, None


# ---------------------------------------------------------------------------
# Behavior events
# ---------------------------------------------------------------------------

def _get_or_create_behavior(session_id: int, data: BehaviorEventCreateRequest) -> tuple[BehaviorEvent, bool]:
    """
    Upserts on (session, client_event_id) when the mobile offline queue sends
    one — closes the crash window where a sync call reaches the server but
    the app dies before the local row can be flagged synced, which would
    otherwise resend and duplicate on retry (unlike trials, behavior events
    have no natural (target, trial_number) key to dedupe on). Falls back to
    a plain create when no client_event_id is sent (e.g. from the web app,
    which has no offline queue and nothing to dedupe against).
    """
    payload = data.dict()
    client_event_id = payload.pop('client_event_id', None)
    if client_event_id:
        return BehaviorEvent.objects.get_or_create(
            session_run_id=session_id,
            client_event_id=client_event_id,
            defaults=payload,
        )
    return BehaviorEvent.objects.create(session_run_id=session_id, **payload), True


@router.get('/sessions/{session_id}/behaviors', response=list[BehaviorEventSchema])
def list_behaviors(request, session_id: int):
    _get_session_or_404(session_id, request)
    return list(BehaviorEvent.objects.filter(session_run_id=session_id))


@router.post('/sessions/{session_id}/behaviors', response={201: BehaviorEventSchema})
def add_behavior(request, session_id: int, data: BehaviorEventCreateRequest):
    session = _get_session_or_404(session_id, request)
    if not session.is_editable:
        raise HttpError(409, f'Session is {session.status} — cannot add behavior events')
    event, _ = _get_or_create_behavior(session_id, data)
    return 201, event


@router.delete('/sessions/{session_id}/behaviors/{event_id}', response={204: None})
def delete_behavior(request, session_id: int, event_id: int):
    session = _get_session_or_404(session_id, request)
    if not session.is_editable:
        raise HttpError(409, f'Session is {session.status} — cannot delete behavior events')
    try:
        BehaviorEvent.objects.get(id=event_id, session_run_id=session_id).delete()
    except BehaviorEvent.DoesNotExist:
        raise HttpError(404, 'Behavior event not found')
    return 204, None


# ---------------------------------------------------------------------------
# ABC events
# ---------------------------------------------------------------------------

def _get_or_create_abc(session_id: int, data: ABCEventCreateRequest) -> tuple[ABCEvent, bool]:
    """Same crash-window dedup purpose as _get_or_create_behavior — this is
    the endpoint that matters most for it, since ABC events sync through
    this individual endpoint rather than the batch /sync one."""
    payload = data.dict()
    client_event_id = payload.pop('client_event_id', None)
    if client_event_id:
        return ABCEvent.objects.get_or_create(
            session_run_id=session_id,
            client_event_id=client_event_id,
            defaults=payload,
        )
    return ABCEvent.objects.create(session_run_id=session_id, **payload), True


@router.get('/sessions/{session_id}/abc', response=list[ABCEventSchema])
def list_abc(request, session_id: int):
    _get_session_or_404(session_id, request)
    return list(ABCEvent.objects.filter(session_run_id=session_id))


@router.post('/sessions/{session_id}/abc', response={201: ABCEventSchema})
def add_abc(request, session_id: int, data: ABCEventCreateRequest):
    session = _get_session_or_404(session_id, request)
    if not session.is_editable:
        raise HttpError(409, f'Session is {session.status} — cannot add ABC events')
    event, _ = _get_or_create_abc(session_id, data)
    return 201, event


@router.delete('/sessions/{session_id}/abc/{event_id}', response={204: None})
def delete_abc(request, session_id: int, event_id: int):
    session = _get_session_or_404(session_id, request)
    if not session.is_editable:
        raise HttpError(409, f'Session is {session.status} — cannot delete ABC events')
    try:
        ABCEvent.objects.get(id=event_id, session_run_id=session_id).delete()
    except ABCEvent.DoesNotExist:
        raise HttpError(404, 'ABC event not found')
    return 204, None


# ---------------------------------------------------------------------------
# Session workflow — submit / approve / reject
# ---------------------------------------------------------------------------

@router.post('/sessions/{session_id}/submit', response=SessionSubmitResponse)
def submit(request, session_id: int, data: SessionSubmitRequest):
    session = _get_session_or_404(session_id, request)
    if data.ended_at:
        session.ended_at = data.ended_at
    advanced, faded = submit_session(session, request.user)
    return {
        'session': _serialize_session(session),
        'advanced_targets': [
            TargetAdvancedSchema(
                name=t.name,
                from_status=t._pre_advance_status,
                to_status=t.status,
            )
            for t in advanced
        ],
        'faded_targets': [
            TargetFadedSchema(
                name=t.name,
                from_level_label=t._pre_fade_from_label,
                to_level_label=t._pre_fade_to_label,
            )
            for t in faded
        ],
    }


@router.post('/sessions/{session_id}/approve', response=SessionRunSchema)
def approve(request, session_id: int):
    require_permission(request, 'session_approve')
    session = _get_session_or_404(session_id, request)
    approve_session(session, request.user)
    return _serialize_session(session)


@router.post('/sessions/{session_id}/reject', response=SessionRunSchema)
def reject(request, session_id: int, data: SessionRejectRequest):
    require_permission(request, 'session_approve')
    session = _get_session_or_404(session_id, request)
    reject_session(session, request.user, data.reason)
    return _serialize_session(session)


# ---------------------------------------------------------------------------
# Offline batch sync — mobile sends everything in one shot after connectivity restored
# ---------------------------------------------------------------------------

@router.post('/sessions/{session_id}/sync', response=SessionSyncResult)
def sync_session(request, session_id: int, data: SessionSyncPayload):
    """
    Idempotent batch endpoint for the mobile offline workflow.

    Mobile stores all events in local SQLite during an offline session, then
    calls this endpoint once back online. Safe to call multiple times:
    trials dedupe on (session, target_id, trial_number, sub_item_key);
    behaviors/abc dedupe on (session, client_event_id) when the mobile queue
    sends one — see _get_or_create_behavior/_get_or_create_abc.
    """
    session = _get_session_or_404(session_id, request)
    if not session.is_editable:
        raise HttpError(409, f'Session is {session.status} — sync not allowed')

    if data.ended_at:
        session.ended_at = data.ended_at
        session.save(update_fields=['ended_at'])

    trials_created = 0
    for t in data.trials:
        _, created = TrialEvent.objects.get_or_create(
            session_run_id=session_id,
            target_id=t.target_id,
            trial_number=t.trial_number,
            sub_item_key=t.sub_item_key,
            defaults={
                'target_name': t.target_name,
                'response_score': t.response_score,
                'prompt_level_label': t.prompt_level_label,
                'recorded_at': t.recorded_at,
                'staff_notes': t.staff_notes,
            },
        )
        if created:
            trials_created += 1

    behaviors_created = 0
    for b in data.behaviors:
        _, created = _get_or_create_behavior(session_id, b)
        if created:
            behaviors_created += 1

    abc_created = 0
    for a in data.abc:
        _, created = _get_or_create_abc(session_id, a)
        if created:
            abc_created += 1

    submitted = False
    if data.submit_after_sync:
        submit_session(session, request.user)
        submitted = True

    return SessionSyncResult(
        trials_created=trials_created,
        behaviors_created=behaviors_created,
        abc_created=abc_created,
        submitted=submitted,
    )


# ---------------------------------------------------------------------------
# Session media — photo/video attachments + async supervision review
# ---------------------------------------------------------------------------

def _serialize_media(media: SessionMedia, request) -> SessionMediaSchema:
    return SessionMediaSchema(
        id=media.id,
        session_run_id=media.session_run_id,
        target_id=media.target_id,
        target_name=media.target_name,
        media_type=media.media_type,
        file_url=request.build_absolute_uri(media.file.url),
        duration_seconds=media.duration_seconds,
        caption=media.caption,
        review_status=media.review_status,
        reviewed_by=media.reviewed_by.email if media.reviewed_by_id else None,
        reviewed_at=media.reviewed_at,
        uploaded_by=media.created_by.email if media.created_by_id else None,
        created_at=media.created_at,
        comment_count=media.comments.count(),
    )


def _get_media_or_404(media_id: int, request) -> SessionMedia:
    """Same ownership rule as _get_session_or_404 — staff only reach media on their own sessions."""
    qs = SessionMedia.objects.select_related('session_run', 'created_by', 'reviewed_by')
    if request.user.role == 'staff':
        qs = qs.filter(session_run__staff_id=request.user.id)
    try:
        return qs.get(id=media_id)
    except SessionMedia.DoesNotExist:
        raise HttpError(404, 'Media not found')


@router.get('/sessions/{session_id}/media', response=list[SessionMediaSchema])
def list_session_media(request, session_id: int):
    session = _get_session_or_404(session_id, request)
    media = session.media.select_related('created_by', 'reviewed_by')
    return [_serialize_media(m, request) for m in media]


@router.post('/sessions/{session_id}/media', response={201: SessionMediaSchema})
def upload_session_media(
    request,
    session_id: int,
    file: UploadedFile = File(...),
    media_type: str = Form(...),
    target_id: int | None = Form(None),
    target_name: str = Form(''),
    caption: str = Form(''),
    duration_seconds: int | None = Form(None),
):
    session = _get_session_or_404(session_id, request)
    if media_type not in SessionMedia.MediaType.values:
        raise HttpError(400, f'media_type must be one of {SessionMedia.MediaType.values}')

    media = SessionMedia.objects.create(
        session_run=session,
        target_id=target_id,
        target_name=target_name,
        media_type=media_type,
        file=file,
        duration_seconds=duration_seconds,
        caption=caption,
        created_by=request.user,
    )
    return 201, _serialize_media(media, request)


@router.patch('/media/{media_id}', response=SessionMediaSchema)
def update_session_media(request, media_id: int, data: SessionMediaUpdateRequest):
    media = _get_media_or_404(media_id, request)
    is_reviewer = request.user.role in ('admin', 'supervisor')
    is_uploader = media.created_by_id == request.user.id

    if data.review_status is not None:
        if not is_reviewer:
            raise HttpError(403, 'Only a supervisor or admin can change review status')
        if data.review_status not in SessionMedia.ReviewStatus.values:
            raise HttpError(400, f'review_status must be one of {SessionMedia.ReviewStatus.values}')
        media.review_status = data.review_status
        media.reviewed_by = request.user
        media.reviewed_at = timezone.now()

    if data.caption is not None:
        if not (is_reviewer or is_uploader):
            raise HttpError(403, 'Only the uploader, a supervisor, or an admin can edit the caption')
        media.caption = data.caption

    media.save()
    return _serialize_media(media, request)


@router.delete('/media/{media_id}', response={204: None})
def delete_session_media(request, media_id: int):
    media = _get_media_or_404(media_id, request)
    if not (request.user.role in ('admin', 'supervisor') or media.created_by_id == request.user.id):
        raise HttpError(403, 'Only the uploader, a supervisor, or an admin can delete this')
    media.file.delete(save=False)
    media.delete()
    return 204, None


@router.get('/media/{media_id}/comments', response=list[SessionMediaCommentSchema])
def list_media_comments(request, media_id: int):
    media = _get_media_or_404(media_id, request)
    return [
        SessionMediaCommentSchema(
            id=c.id,
            session_media_id=c.session_media_id,
            timestamp_seconds=c.timestamp_seconds,
            body=c.body,
            author=c.created_by.email if c.created_by_id else None,
            created_at=c.created_at,
        )
        for c in media.comments.select_related('created_by')
    ]


@router.post('/media/{media_id}/comments', response={201: SessionMediaCommentSchema})
def create_media_comment(request, media_id: int, data: SessionMediaCommentCreateRequest):
    media = _get_media_or_404(media_id, request)
    comment = SessionMediaComment.objects.create(
        session_media=media,
        timestamp_seconds=data.timestamp_seconds,
        body=data.body,
        created_by=request.user,
    )
    return 201, SessionMediaCommentSchema(
        id=comment.id,
        session_media_id=comment.session_media_id,
        timestamp_seconds=comment.timestamp_seconds,
        body=comment.body,
        author=request.user.email,
        created_at=comment.created_at,
    )
