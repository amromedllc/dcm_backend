from datetime import date
from django.db.models import Count
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from .models import Appointment, SessionRun, TrialEvent, BehaviorEvent, ABCEvent
from .schemas import (
    AppointmentSchema, AppointmentCreateRequest, AppointmentUpdateRequest,
    AssignProgramsRequest, AssignedProgramSchema,
    SessionRunSchema, SessionStartRequest, SessionSubmitRequest, SessionRejectRequest,
    SessionSubmitResponse, TargetAdvancedSchema,
    TrialEventSchema, TrialEventCreateRequest,
    BehaviorEventSchema, BehaviorEventCreateRequest,
    ABCEventSchema, ABCEventCreateRequest,
    SessionSyncPayload, SessionSyncResult,
    TrialSummaryItem,
)
from .services import build_program_snapshot, submit_session, approve_session, reject_session

router = Router(auth=jwt_auth)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_session_or_404(session_id: int, request) -> SessionRun:
    """
    Staff may only reach their own sessions (matches the scoping already
    applied in list_sessions below) — admin/supervisor can reach any session
    within this tenant's schema. Without this, any staff user could read,
    edit, or delete another staff member's session (and its trial/behavior/
    ABC data) purely by guessing/incrementing a session id, since every
    caller of this helper — get/delete session, trials, behaviors, abc,
    submit, sync — otherwise had no ownership check at all.
    """
    qs = SessionRun.objects.select_related('staff')
    if request.user.role == 'staff':
        qs = qs.filter(staff_id=request.user.id)
    try:
        return qs.get(id=session_id)
    except SessionRun.DoesNotExist:
        raise HttpError(404, 'Session not found')


def _build_trial_summary(session_run: SessionRun) -> list[TrialSummaryItem]:
    from django.db.models import Sum, Q
    rows = (
        session_run.trial_events
        .values('target_id', 'target_name')
        .annotate(
            total_trials=Count('id'),
            # A trial is "correct" when it scores the maximum level (Independent)
            # The threshold is determined by the snapshot's prompting template.
            # For simplicity: score > 0 treated as correct at analytics level.
            # Stage 5 analytics will do full mastery-template-aware scoring.
        )
    )
    result = []
    for row in rows:
        trials = session_run.trial_events.filter(target_id=row['target_id'])
        total = trials.count()
        # Resolve max score from snapshot
        max_score = _max_score_for_target(session_run.program_snapshot, row['target_id'])
        correct = trials.filter(response_score=max_score).count() if max_score is not None else 0
        result.append(TrialSummaryItem(
            target_id=row['target_id'],
            target_name=row['target_name'],
            total_trials=total,
            correct_count=correct,
            pct_correct=round((correct / total * 100), 1) if total else 0.0,
        ))
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
    from apps.legacy.models import TpmsAppointment
    return TpmsAppointment.objects.using('therapypms').filter(id=external_appointment_id).first()


def _aware(dt):
    if dt is None:
        return None
    from django.utils import timezone
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _serialize_session(session: SessionRun, tpms_appt=None) -> dict:
    staff = session.staff
    staff_name = f'{staff.first_name} {staff.last_name}'.strip() if staff else None
    if tpms_appt is None and session.external_appointment_id:
        tpms_appt = _get_tpms_appointment(session.external_appointment_id)
    return {
        'id': session.id,
        'client_id': session.external_client_id,
        'staff_id': session.staff_id,
        'staff_name': staff_name or (staff.email if staff else None),
        'appointment_id': session.external_appointment_id,
        'appointment_start_time': _aware(tpms_appt.from_time) if tpms_appt else None,
        'appointment_end_time': _aware(tpms_appt.to_time) if tpms_appt else None,
        'lesson_id': session.lesson_id,
        'status': session.status,
        'started_at': session.started_at,
        'ended_at': session.ended_at,
        'submitted_at': session.submitted_at,
        'reviewed_at': session.reviewed_at,
        'rejection_reason': session.rejection_reason,
        'program_snapshot': session.program_snapshot,
        'trial_summary': _build_trial_summary(session),
        'behavior_event_count': session.behavior_events.count(),
        'abc_event_count': session.abc_events.count(),
        'created_at': session.created_at,
    }


# ---------------------------------------------------------------------------
# TPMS-direct provider appointments (read-only, no sync required)
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


@router.get('/provider-appointments', response=list[AppointmentSchema])
def list_provider_appointments(
    request,
    external_employee_id: int,
    status: str | None = None,
):
    """Return appointments from TPMS directly for a given provider (employee) ID."""
    from apps.legacy.models import TpmsAppointment, TpmsActivityTemplate
    from django.utils import timezone as tz

    qs = list(TpmsAppointment.objects.using('therapypms').filter(
        provider_id=external_employee_id,
    ).order_by('-schedule_date'))

    # Batch-fetch activity templates for service names
    act_ids = {a.authorization_activity_id for a in qs if a.authorization_activity_id}
    activity_map = {}
    if act_ids:
        for act in TpmsActivityTemplate.objects.using('therapypms').filter(id__in=act_ids):
            activity_map[act.id] = act.activity_name or ''

    def _aware(dt):
        if dt is None:
            return None
        from django.utils import timezone
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt

    from datetime import datetime as dt_cls
    results = []
    for appt in qs:
        if appt.from_time:
            start = appt.from_time
        elif appt.schedule_date:
            start = dt_cls(appt.schedule_date.year, appt.schedule_date.month, appt.schedule_date.day)
        else:
            continue
        mapped_status = _tpms_status(appt.status)
        if status and mapped_status != status:
            continue
        end = appt.to_time or start

        service_name = (
            activity_map.get(appt.authorization_activity_id)
            or appt.activity_type
            or appt.cpt_code
            or ''
        )
        duration_mins = appt.time_duration
        if not duration_mins and appt.from_time and appt.to_time:
            delta = appt.to_time - appt.from_time
            duration_mins = int(delta.total_seconds() / 60)

        results.append({
            'id': appt.id,
            'client_id': appt.client_id or 0,
            'staff_id': None,
            'lesson_id': None,
            'assigned_program_count': 0,
            'external_id': str(appt.id),
            'source': 'tpms',
            'start_time': _aware(start),
            'end_time': _aware(end),
            'service_type': service_name,
            'duration_minutes': duration_mins or 0,
            'notes': appt.notes or '',
            'status': mapped_status,
            'synced_at': None,
            'created_at': _aware(appt.created_at) or _aware(start),
        })
    return results


@router.get('/my-schedule', response=list[AppointmentSchema])
def my_schedule(request, date: str | None = None):
    """
    Return TPMS appointments for the logged-in staff member on a given date
    (defaults to today). Resolves the TPMS employee record via email match.
    """
    from apps.legacy.models import TpmsAppointment, TpmsActivityTemplate
    from django.utils import timezone as tz
    from datetime import date as dt_date

    target_date = date or dt_date.today().isoformat()

    employee_id = request.user.external_employee_id
    if employee_id is None:
        # Native (non-TPMS) staff: read straight from the local Appointment table.
        return list(
            _appt_qs()
            .filter(staff_id=request.user.id, start_time__date=target_date)
            .order_by('start_time')
        )

    qs = list(
        TpmsAppointment.objects.using('therapypms').filter(
            provider_id=employee_id,
            schedule_date=target_date,
        ).exclude(status__in=['deleted', 'void', 'voided']).order_by('from_time')
    )

    if not qs:
        return []

    act_ids = {a.authorization_activity_id for a in qs if a.authorization_activity_id}
    activity_map: dict[int, str] = {}
    if act_ids:
        for act in TpmsActivityTemplate.objects.using('therapypms').filter(id__in=act_ids):
            activity_map[act.id] = act.activity_name or act.cpt_code or ''

    from apps.legacy.models import TpmsClient
    client_ids = {a.client_id for a in qs if a.client_id}
    client_map: dict[int, str] = {}
    if client_ids:
        for c in TpmsClient.objects.using('therapypms').filter(id__in=client_ids):
            client_map[c.id] = (
                c.client_full_name
                or f'{c.client_first_name or ""} {c.client_last_name or ""}'.strip()
                or f'Client {c.id}'
            )

    def _aware(dt):
        if dt is None:
            return None
        return tz.make_aware(dt) if tz.is_naive(dt) else dt

    from datetime import datetime as dt_cls
    results = []
    for appt in qs:
        if appt.from_time:
            start = appt.from_time
        elif appt.schedule_date:
            start = dt_cls(appt.schedule_date.year, appt.schedule_date.month, appt.schedule_date.day)
        else:
            continue
        end = appt.to_time or start
        service_name = (
            activity_map.get(appt.authorization_activity_id)
            or appt.activity_type or appt.cpt_code or ''
        )
        duration_mins = appt.time_duration
        if not duration_mins and appt.from_time and appt.to_time:
            delta = appt.to_time - appt.from_time
            duration_mins = int(delta.total_seconds() / 60)
        results.append({
            'id': appt.id,
            'client_id': appt.client_id or 0,
            'client_name': client_map.get(appt.client_id) if appt.client_id else None,
            'staff_id': None,
            'staff_name': None,
            'lesson_id': None,
            'assigned_program_count': 0,
            'external_id': str(appt.id),
            'source': 'tpms',
            'start_time': _aware(start),
            'end_time': _aware(end),
            'service_type': service_name,
            'location': appt.location or None,
            'duration_minutes': duration_mins or 0,
            'notes': appt.notes or '',
            'status': _tpms_status(appt.status),
            'synced_at': None,
            'created_at': _aware(appt.created_at) or _aware(start),
        })
    return results


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

def _appt_qs():
    return Appointment.objects.annotate(
        assigned_program_count=Count('lesson__lesson_programs', distinct=True)
    )


@router.get('/appointments', response=list[AppointmentSchema])
def list_appointments(
    request,
    client_id: int | None = None,
    staff_id: int | None = None,
    date: str | None = None,
    status: str | None = None,
):
    qs = _appt_qs()
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
    if request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')
    payload = data.dict()
    external_client_id = payload.pop('client_id', None)
    appt = Appointment.objects.create(created_by=request.user, external_client_id=external_client_id, **payload)
    return 201, appt


@router.get('/appointments/{appt_id}', response=AppointmentSchema)
def get_appointment(request, appt_id: int):
    try:
        return _appt_qs().get(id=appt_id)
    except Appointment.DoesNotExist:
        raise HttpError(404, 'Appointment not found')


@router.get('/appointments/{appt_id}/programs', response=list[AssignedProgramSchema])
def get_appointment_programs(request, appt_id: int):
    """Returns programs currently assigned to this appointment."""
    appt = (
        Appointment.objects.filter(id=appt_id).select_related('lesson').first()
        or Appointment.objects.filter(external_id=str(appt_id)).select_related('lesson').first()
    )
    if not appt:
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
    If no DCM Appointment row exists yet, one is created on demand from TPMS data.
    """
    if request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')

    appt = (
        Appointment.objects
        .filter(id=appt_id)
        .select_related('lesson')
        .first()
    ) or (
        Appointment.objects
        .filter(external_id=str(appt_id))
        .select_related('lesson')
        .first()
    )

    if not appt:
        # No DCM record yet — create one from TPMS data
        from apps.legacy.models import TpmsAppointment
        from django.utils import timezone as tz

        tpms_appt = TpmsAppointment.objects.using('therapypms').filter(id=appt_id).first()
        if not tpms_appt or not tpms_appt.from_time:
            raise HttpError(404, 'Appointment not found')

        def _aware(dt):
            if dt is None:
                return None
            return tz.make_aware(dt) if tz.is_naive(dt) else dt

        appt = Appointment.objects.create(
            external_id=str(appt_id),
            external_client_id=data.client_id,
            source=Appointment.Source.SYNCED,
            start_time=_aware(tpms_appt.from_time),
            end_time=_aware(tpms_appt.to_time or tpms_appt.from_time),
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
                external_client_id=data.client_id,
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
    if request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')
    try:
        appt = Appointment.objects.get(id=appt_id)
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
    qs = SessionRun.objects.select_related('staff')
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
    if appt_ids:
        from apps.legacy.models import TpmsAppointment
        tpms_appts = {a.id: a for a in TpmsAppointment.objects.using('therapypms').filter(id__in=appt_ids)}
    else:
        tpms_appts = {}
    return [_serialize_session(s, tpms_appts.get(s.external_appointment_id)) for s in sessions]


@router.get('/sessions/{session_id}', response=SessionRunSchema)
def get_session(request, session_id: int):
    session = _get_session_or_404(session_id, request)
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

@router.get('/sessions/{session_id}/behaviors', response=list[BehaviorEventSchema])
def list_behaviors(request, session_id: int):
    _get_session_or_404(session_id, request)
    return list(BehaviorEvent.objects.filter(session_run_id=session_id))


@router.post('/sessions/{session_id}/behaviors', response={201: BehaviorEventSchema})
def add_behavior(request, session_id: int, data: BehaviorEventCreateRequest):
    session = _get_session_or_404(session_id, request)
    if not session.is_editable:
        raise HttpError(409, f'Session is {session.status} — cannot add behavior events')
    event = BehaviorEvent.objects.create(session_run_id=session_id, **data.dict())
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

@router.get('/sessions/{session_id}/abc', response=list[ABCEventSchema])
def list_abc(request, session_id: int):
    _get_session_or_404(session_id, request)
    return list(ABCEvent.objects.filter(session_run_id=session_id))


@router.post('/sessions/{session_id}/abc', response={201: ABCEventSchema})
def add_abc(request, session_id: int, data: ABCEventCreateRequest):
    session = _get_session_or_404(session_id, request)
    if not session.is_editable:
        raise HttpError(409, f'Session is {session.status} — cannot add ABC events')
    event = ABCEvent.objects.create(session_run_id=session_id, **data.dict())
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
    advanced = submit_session(session, request.user)
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
    }


@router.post('/sessions/{session_id}/approve', response=SessionRunSchema)
def approve(request, session_id: int):
    if request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')
    session = _get_session_or_404(session_id, request)
    approve_session(session, request.user)
    return _serialize_session(session)


@router.post('/sessions/{session_id}/reject', response=SessionRunSchema)
def reject(request, session_id: int, data: SessionRejectRequest):
    if request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')
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
    calls this endpoint once back online. Duplicate trial entries (same session +
    target_id + trial_number) are skipped — safe to call multiple times.
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
        event = BehaviorEvent.objects.create(session_run_id=session_id, **b.dict())
        behaviors_created += 1

    abc_created = 0
    for a in data.abc:
        event = ABCEvent.objects.create(session_run_id=session_id, **a.dict())
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
