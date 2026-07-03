from datetime import date
from ninja import Router
from ninja.errors import HttpError
from django.db.models import Q, Count

from apps.accounts.auth import jwt_auth
from apps.sessions.schemas import AppointmentSchema
from .models import Client, ClientStaffAssignment
from .schemas import (
    ClientSchema,
    ClientCreateRequest,
    ClientUpdateRequest,
    StaffAssignmentSchema,
    AddStaffAssignmentRequest,
)

router = Router(auth=jwt_auth)


def _get_accessible_clients(request):
    """
    Return the client queryset visible to the requesting user.

    - Admins/supervisors: all clients in their TPMS practice scope.
    - Staff: clients they have TPMS appointments with (matched via provider_id).
    """
    qs = Client.objects.all()

    if request.user.tpms_admin_id is not None:
        qs = qs.filter(tpms_admin_id=request.user.tpms_admin_id)

    if request.user.role in ('admin', 'supervisor'):
        return qs

    # Staff: derive accessible clients from their TPMS appointment history
    from apps.legacy.models import TpmsAppointment
    employee_id = request.user.tpms_employee_id
    if not employee_id:
        return qs.none()

    tpms_client_ids = (
        TpmsAppointment.objects.using('therapypms')
        .filter(provider_id=employee_id)
        .exclude(status__in=['deleted', 'void', 'voided'])
        .exclude(client_id__isnull=True)
        .values_list('client_id', flat=True)
        .distinct()
    )
    accessible_ext_ids = [str(cid) for cid in tpms_client_ids]
    return qs.filter(external_id__in=accessible_ext_ids)


def _get_client_or_404(request, client_id: int) -> Client:
    qs = _get_accessible_clients(request)
    try:
        return qs.get(id=client_id)
    except Client.DoesNotExist:
        raise HttpError(404, 'Client not found')


# ---------------------------------------------------------------------------
# Client CRUD
# ---------------------------------------------------------------------------

@router.get('', response=list[ClientSchema])
def list_clients(request, include_inactive: bool = False, search: str | None = None):
    """
    Returns patients scoped to the logged-in user's TPMS practice.

    Mirrors the TherapyPMS pattern: reads live from the TPMS clients table
    filtered by admin_id, then auto-creates DCM Client records for any not yet
    synced so that programs/sessions can link against a stable DCM id.

    - Admin/supervisor: all patients in their practice.
    - Staff: only patients they have been explicitly assigned to.
    """
    if request.user.tpms_admin_id is None:
        return []

    from apps.legacy.models import TpmsClient

    tpms_qs = TpmsClient.objects.using('therapypms').filter(
        admin_id=request.user.tpms_admin_id,
    )
    if not include_inactive:
        tpms_qs = tpms_qs.filter(is_active_client=1)
    if search:
        tpms_qs = tpms_qs.filter(
            Q(client_first_name__icontains=search)
            | Q(client_last_name__icontains=search)
            | Q(client_full_name__icontains=search)
        )
    tpms_clients = list(tpms_qs.order_by('client_last_name', 'client_first_name'))

    if not tpms_clients:
        return []

    # Resolve external_id → DCM Client in one query
    ext_ids = [str(tc.pk) for tc in tpms_clients]
    existing = {
        c.external_id: c
        for c in Client.objects.filter(external_id__in=ext_ids)
    }

    # For staff: show only clients they have TPMS appointments with
    if request.user.role not in ('admin', 'supervisor'):
        from apps.legacy.models import TpmsAppointment
        employee_id = request.user.tpms_employee_id
        if not employee_id:
            return []
        tpms_client_ids = (
            TpmsAppointment.objects.using('therapypms')
            .filter(provider_id=employee_id)
            .exclude(status__in=['deleted', 'void', 'voided'])
            .exclude(client_id__isnull=True)
            .values_list('client_id', flat=True)
            .distinct()
        )
        assigned_ext_ids = {str(cid) for cid in tpms_client_ids}
        tpms_clients = [tc for tc in tpms_clients if str(tc.pk) in assigned_ext_ids]

    result = []
    for tc in tpms_clients:
        ext_id = str(tc.pk)
        dcm_client = existing.get(ext_id)

        # Resolve best available name from TPMS fields
        first = (tc.client_first_name or '').strip()
        last  = (tc.client_last_name  or '').strip()
        if not first and not last:
            full = (tc.client_full_name or '').strip()
            parts = full.split(' ', 1)
            first = parts[0] if parts else 'Unknown'
            last  = parts[1] if len(parts) > 1 else ''
        first = first or 'Unknown'
        last  = last  or 'Unknown'

        if dcm_client is None:
            dcm_client = Client.objects.create(
                external_id=ext_id,
                first_name=first,
                last_name=last,
                preferred_name=(tc.client_preferred or '').strip(),
                date_of_birth=tc.client_dob,
                status=Client.Status.ACTIVE,
                tpms_admin_id=tc.admin_id,
            )
        else:
            # Always keep names in sync with TPMS
            update_fields = []
            if dcm_client.first_name != first:
                dcm_client.first_name = first
                update_fields.append('first_name')
            if dcm_client.last_name != last:
                dcm_client.last_name = last
                update_fields.append('last_name')
            if update_fields:
                dcm_client.save(update_fields=update_fields)

        result.append(dcm_client)

    return result


@router.post('', response={201: ClientSchema})
def create_client(request, data: ClientCreateRequest):
    if not request.user.role in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')
    client = Client.objects.create(
        created_by=request.user,
        **data.dict(),
    )
    return 201, client


@router.get('/{client_id}', response=ClientSchema)
def get_client(request, client_id: int):
    return _get_client_or_404(request, client_id)


@router.patch('/{client_id}', response=ClientSchema)
def update_client(request, client_id: int, data: ClientUpdateRequest):
    if not request.user.role in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')
    client = _get_client_or_404(request, client_id)
    for field, value in data.dict(exclude_none=True).items():
        setattr(client, field, value)
    client.save()
    return client


# ---------------------------------------------------------------------------
# Staff assignments
# ---------------------------------------------------------------------------

@router.get('/{client_id}/staff', response=list[StaffAssignmentSchema])
def list_staff(request, client_id: int):
    if not request.user.role in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')
    _get_client_or_404(request, client_id)
    return list(ClientStaffAssignment.objects.filter(client_id=client_id, is_active=True))


@router.post('/{client_id}/staff', response={201: StaffAssignmentSchema})
def add_staff(request, client_id: int, data: AddStaffAssignmentRequest):
    if not request.user.role in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')
    _get_client_or_404(request, client_id)
    assignment, created = ClientStaffAssignment.objects.get_or_create(
        client_id=client_id,
        user_id=data.user_id,
        defaults={'is_primary': data.is_primary},
    )
    if not created:
        assignment.is_active = True
        assignment.is_primary = data.is_primary
        assignment.save(update_fields=['is_active', 'is_primary'])
    return 201, assignment


@router.delete('/{client_id}/staff/{assignment_id}', response={204: None})
def remove_staff(request, client_id: int, assignment_id: int):
    if not request.user.role in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')
    _get_client_or_404(request, client_id)
    try:
        assignment = ClientStaffAssignment.objects.get(id=assignment_id, client_id=client_id)
    except ClientStaffAssignment.DoesNotExist:
        raise HttpError(404, 'Assignment not found')
    assignment.is_active = False
    assignment.save(update_fields=['is_active'])
    return 204, None


# ---------------------------------------------------------------------------
# Client sessions — live from TPMS, same pattern as patient list
# ---------------------------------------------------------------------------

_TPMS_EXCLUDED_STATUSES = {'deleted', 'void', 'voided'}

def _tpms_status(raw: str | None) -> str:
    s = (raw or '').lower()
    if s in ('rendered', 'completed', 'kept'):
        return 'completed'
    if s in ('cancelled', 'canceled'):
        return 'cancelled'
    if s in ('no show', 'no-show', 'noshow'):
        return 'no_show'
    return 'scheduled'


@router.get('/{client_id}/sessions', response=list[AppointmentSchema])
def list_client_sessions(
    request,
    client_id: int,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
):
    """
    Return appointments for a client, fetched live from the TPMS appoinments
    table using the client's external_id as the TPMS client_id.

    Mirrors the TherapyPMS pattern: data flows from TPMS → DCM on demand,
    so appointments are always current without requiring a separate sync run.
    """
    from apps.legacy.models import TpmsAppointment, TpmsActivityTemplate
    from django.utils import timezone as tz

    client = _get_client_or_404(request, client_id)

    if not client.external_id:
        return []

    tpms_client_id = int(client.external_id)

    qs = TpmsAppointment.objects.using('therapypms').filter(
        client_id=tpms_client_id,
    ).filter(
        Q(is_break__isnull=True) | Q(is_break=1)
    ).order_by('-schedule_date')

    # Staff see only their own appointments for this client
    if request.user.role not in ('admin', 'supervisor'):
        employee_id = request.user.tpms_employee_id
        if not employee_id:
            return []
        qs = qs.filter(provider_id=employee_id)

    if from_date:
        qs = qs.filter(schedule_date__gte=from_date)
    if to_date:
        qs = qs.filter(schedule_date__lte=to_date)

    appts = list(qs)

    if not appts:
        return []

    from apps.legacy.models import TpmsEmployee

    # Batch-fetch activity template names + CPT codes
    act_ids = {a.authorization_activity_id for a in appts if a.authorization_activity_id}
    activity_map: dict[int, str] = {}
    if act_ids:
        for act in TpmsActivityTemplate.objects.using('therapypms').filter(id__in=act_ids):
            activity_map[act.id] = act.activity_name or act.cpt_code or ''

    # Batch-fetch provider names
    provider_ids = {a.provider_id for a in appts if a.provider_id}
    provider_map: dict[int, str] = {}
    if provider_ids:
        for emp in TpmsEmployee.objects.using('therapypms').filter(id__in=provider_ids):
            provider_map[emp.id] = emp.full_name or f'{emp.first_name or ""} {emp.last_name or ""}'.strip() or ''

    def _aware(dt):
        if dt is None:
            return None
        return tz.make_aware(dt) if tz.is_naive(dt) else dt

    # Merge in DCM appointment data (lesson_id, assigned_program_count, DCM id)
    from apps.sessions.models import Appointment as DcmAppointment
    from datetime import datetime as dt_cls
    tpms_ext_ids = [str(a.id) for a in appts]
    dcm_by_ext: dict[str, DcmAppointment] = {}
    if tpms_ext_ids:
        for dcm in (
            DcmAppointment.objects
            .filter(external_id__in=tpms_ext_ids)
            .annotate(_program_count=Count('lesson__lesson_programs', distinct=True))
        ):
            dcm_by_ext[dcm.external_id] = dcm

    results = []
    for appt in appts:
        # Skip soft-deleted / voided records from TPMS
        if (appt.status or '').lower() in _TPMS_EXCLUDED_STATUSES:
            continue
        mapped_status = _tpms_status(appt.status)
        if status and mapped_status != status:
            continue

        # Use from_time when set; fall back to midnight on schedule_date
        if appt.from_time:
            start = appt.from_time
        elif appt.schedule_date:
            start = dt_cls(appt.schedule_date.year, appt.schedule_date.month, appt.schedule_date.day)
        else:
            continue  # no usable time at all — skip

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

        dcm = dcm_by_ext.get(str(appt.id))
        results.append({
            'id':                    dcm.id if dcm else appt.id,
            'client_id':             client_id,
            'staff_id':              appt.provider_id,
            'staff_name':            provider_map.get(appt.provider_id) if appt.provider_id else None,
            'lesson_id':             dcm.lesson_id if dcm else None,
            'assigned_program_count': dcm._program_count if dcm else 0,
            'external_id':           str(appt.id),
            'source':                'tpms',
            'start_time':            _aware(start),
            'end_time':              _aware(end),
            'service_type':          service_name,
            'location':              appt.location or None,
            'duration_minutes':      duration_mins or 0,
            'notes':                 appt.notes or '',
            'status':                mapped_status,
            'synced_at':             None,
            'created_at':            _aware(appt.created_at) or _aware(start),
        })

    return results
