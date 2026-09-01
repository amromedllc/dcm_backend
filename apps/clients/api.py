import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

import redis
from ninja import Router
from ninja.errors import HttpError
from django.conf import settings
from django.db.models import Q, Count

from apps.accounts.auth import jwt_auth
from apps.accounts.permissions import require_permission
from apps.integrations.tpms_auth_client import (
    TpmsAuthError,
    clear_tpms_access_token,
    get_tpms_access_token,
    list_providers,
    list_provider_calendar,
)

logger = logging.getLogger(__name__)

_PATIENT_LIST_CACHE_TTL_SECONDS = 60
_redis_client: 'redis.Redis | None' = None


def _redis() -> 'redis.Redis':
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL)
    return _redis_client


def _cached_list_patients(access_token: str, external_admin_id: int) -> list[dict[str, Any]]:
    cache_key = f'tpms:patient-list:{external_admin_id}'
    try:
        cached = _redis().get(cache_key)
        if cached is not None:
            return json.loads(cached)
    except redis.RedisError:
        logger.warning('Redis unavailable for TPMS patient-list cache; falling back to live fetch', exc_info=True)

    patients = list_providers(access_token)

    try:
        _redis().set(cache_key, json.dumps(patients), ex=_PATIENT_LIST_CACHE_TTL_SECONDS)
    except redis.RedisError:
        logger.warning('Redis unavailable for TPMS patient-list cache; skipping cache write', exc_info=True)

    return patients
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
    - TPMS-linked staff: same practice scope (patient list is already
      token-scoped by TherapyPMS; appointment history no longer comes from DB).
    - Native staff: clients via ClientStaffAssignment.
    """
    qs = Client.objects.all()

    if request.user.external_admin_id is not None:
        qs = qs.filter(external_admin_id=request.user.external_admin_id)

    if request.user.role in ('admin', 'supervisor'):
        return qs

    if request.user.external_admin_id is not None:
        # TPMS-linked staff — practice-scoped (TherapyPMS DB removed)
        return qs

    # Native staff: derive accessible clients from ClientStaffAssignment
    assigned_client_ids = ClientStaffAssignment.objects.filter(
        user=request.user, is_active=True,
    ).values_list('client_id', flat=True)
    return qs.filter(id__in=assigned_client_ids)


def _get_client_or_404(request, client_id: int) -> Client:
    qs = _get_accessible_clients(request)
    try:
        return qs.get(id=client_id)
    except Client.DoesNotExist:
        raise HttpError(404, 'Client not found')


# ---------------------------------------------------------------------------
# Client CRUD
# ---------------------------------------------------------------------------

def _list_native_clients(request, include_inactive: bool, search: str | None) -> list[Client]:
    """Native (non-TPMS) equivalent of list_clients — reads the local Client
    table directly instead of live TPMS data, reusing the same staff-scoping
    logic as _get_accessible_clients."""
    qs = _get_accessible_clients(request)
    if not include_inactive:
        qs = qs.filter(status=Client.Status.ACTIVE)
    if search:
        qs = qs.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(preferred_name__icontains=search)
        )
    return list(qs.order_by('last_name', 'first_name'))


def _map_patient_fields(patient: dict[str, Any], *, fallback_admin_id: int | None) -> dict[str, Any] | None:
    """
    Maps GET /api/v1/ios/appointment/filter/providers rows — {"id", "name"}
    only — onto Client fields. "name" is split on the first space into
    first_name/last_name; with no space, the whole name goes to last_name.
    No DOB/preferred-name/active-status data is available from this
    endpoint, so those default (active, blank preferred name, no DOB).
    """
    ext_id = patient.get('id')
    if ext_id is None:
        return None

    name = str(patient.get('name') or '').strip()
    if ' ' in name:
        first, last = name.split(' ', 1)
        first, last = first.strip(), last.strip()
    else:
        first, last = '', name

    return {
        'external_id': str(ext_id),
        'first_name': first or 'Unknown',
        'last_name': last or 'Unknown',
        'preferred_name': '',
        'date_of_birth': None,
        'status': Client.Status.ACTIVE,
        'external_admin_id': fallback_admin_id,
        'is_active': True,
    }


def _upsert_clients_from_patients(
    patients: list[dict[str, Any]],
    *,
    fallback_admin_id: int | None,
    include_inactive: bool,
    search: str | None,
) -> list[Client]:
    mapped: list[dict[str, Any]] = []
    for patient in patients:
        fields = _map_patient_fields(patient, fallback_admin_id=fallback_admin_id)
        if fields is None:
            continue
        if not include_inactive and not fields['is_active']:
            continue
        if search:
            needle = search.lower()
            hay = f"{fields['first_name']} {fields['last_name']} {fields['preferred_name']}".lower()
            if needle not in hay:
                continue
        mapped.append(fields)

    mapped.sort(key=lambda row: (row['last_name'].lower(), row['first_name'].lower()))
    if not mapped:
        return []

    ext_ids = [row['external_id'] for row in mapped]
    existing = {
        c.external_id: c
        for c in Client.objects.filter(external_id__in=ext_ids)
    }

    update_attrs = ('first_name', 'last_name', 'preferred_name', 'date_of_birth', 'status', 'external_admin_id')
    to_create: list[Client] = []
    to_update: list[Client] = []

    from shared.tenancy import current_org_id_or_none

    org_id = current_org_id_or_none()

    for fields in mapped:
        fields = {k: v for k, v in fields.items() if k != 'is_active'}
        ext_id = fields['external_id']
        dcm_client = existing.get(ext_id)
        if dcm_client is None:
            client = Client(**fields)
            if org_id is not None:
                client.organization_id = org_id
            to_create.append(client)
            existing[ext_id] = client
        else:
            changed = False
            for attr in update_attrs:
                value = fields.get(attr)
                if value is not None and getattr(dcm_client, attr) != value:
                    setattr(dcm_client, attr, value)
                    changed = True
            if changed:
                to_update.append(dcm_client)

    if to_create:
        Client.objects.bulk_create(to_create, batch_size=200)
    if to_update:
        Client.objects.bulk_update(to_update, list(update_attrs), batch_size=200)

    # Prefer DB rows (with PKs) for the response when available.
    persisted = {
        c.external_id: c
        for c in Client.objects.filter(external_id__in=ext_ids)
    }
    result = [persisted[ext_id] for ext_id in ext_ids if ext_id in persisted]
    result.sort(key=lambda c: (c.last_name.lower(), c.first_name.lower()))
    return result


def _sync_clients_from_tpms(
    request,
    *,
    include_inactive: bool,
    search: str | None,
) -> list[Client]:
    """Fetch TPMS providers for this session and upsert into DCM Client rows.

    /api/v1/ios/appointment/filter/providers has no server-side search
    param, so `search` is always applied client-side in
    _upsert_clients_from_patients below."""
    token = get_tpms_access_token(request.user.id)
    if not token:
        raise HttpError(401, 'TherapyPMS session expired. Please log in again.')

    try:
        patients = _cached_list_patients(token, request.user.external_admin_id)
    except TpmsAuthError as exc:
        if exc.status_code in {401, 403}:
            clear_tpms_access_token(request.user.id)
            raise HttpError(401, 'TherapyPMS session expired. Please log in again.') from exc
        raise HttpError(502, str(exc) or 'Failed to load patients from TherapyPMS') from exc

    return _upsert_clients_from_patients(
        patients,
        fallback_admin_id=request.user.external_admin_id,
        include_inactive=include_inactive,
        search=search,
    )


@router.get('', response=list[ClientSchema])
def list_clients(
    request,
    include_inactive: bool = False,
    search: str | None = None,
    sync: bool = True,
):
    """
    Returns providers scoped to the logged-in user's TherapyPMS session.

    Uses GET /api/v1/ios/appointment/filter/providers with the TPMS Bearer
    token captured at login, then upserts DCM Client rows so
    programs/sessions keep a stable id. Note: Client.external_id now holds a
    TPMS *provider* id, not a patient id — the client-sessions endpoint below
    still treats it as a patient id and has not been updated to match.
    """
    if request.user.external_admin_id is None or not sync:
        return _list_native_clients(request, include_inactive, search)

    return _sync_clients_from_tpms(
        request,
        include_inactive=include_inactive,
        search=search,
    )


@router.post('', response={201: ClientSchema})
def create_client(request, data: ClientCreateRequest):
    require_permission(request, 'clients_create')
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
    require_permission(request, 'clients_edit')
    client = _get_client_or_404(request, client_id)
    for field, value in data.dict(exclude_none=True).items():
        setattr(client, field, value)
    if client.discharge_date and client.intake_date and client.discharge_date < client.intake_date:
        raise HttpError(400, 'discharge_date cannot be before intake_date')
    client.save()
    return client


# ---------------------------------------------------------------------------
# Staff assignments
# ---------------------------------------------------------------------------

@router.get('/{client_id}/staff', response=list[StaffAssignmentSchema])
def list_staff(request, client_id: int):
    require_permission(request, 'clients_edit')
    _get_client_or_404(request, client_id)
    return list(ClientStaffAssignment.objects.filter(client_id=client_id, is_active=True))


@router.post('/{client_id}/staff', response={201: StaffAssignmentSchema})
def add_staff(request, client_id: int, data: AddStaffAssignmentRequest):
    require_permission(request, 'clients_edit')
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
    require_permission(request, 'clients_edit')
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


def _dig_appointment(data: dict[str, Any], *keys: str) -> Any:
    def norm(value: str) -> str:
        return ''.join(ch for ch in value.lower() if ch.isalnum())

    for key in keys:
        if key in data and data[key] not in (None, ''):
            return data[key]
        normalized = norm(key)
        for existing, value in data.items():
            if norm(existing) == normalized and value not in (None, ''):
                return value
    for nested_key in ('appointment', 'recurring_appointment', 'session'):
        nested = data.get(nested_key)
        if isinstance(nested, dict):
            value = _dig_appointment(nested, *keys)
            if value not in (None, ''):
                return value
    return None


def _notes_text(value: Any) -> str:
    """/ios/calendar's "comment" field uses the literal string "none" as its
    empty placeholder rather than null/blank — treat that as no notes."""
    text = str(value or '').strip()
    return '' if text.lower() == 'none' else text


def _parse_appointment_datetime(value: Any) -> datetime | None:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        pass
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%m/%d/%Y %H:%M:%S', '%m/%d/%Y'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_appointment_date_only(value: Any) -> date | None:
    parsed = _parse_appointment_datetime(value)
    if parsed is not None:
        return parsed.date()
    return None


def _parse_clock_time(value: str) -> tuple[int, int] | None:
    """Parse a clock string like '10:00 am' into (hour, minute)."""
    text = str(value or '').strip().lower()
    if not text:
        return None
    for fmt in ('%I:%M %p', '%I %p', '%H:%M', '%I:%M%p'):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.hour, parsed.minute
        except ValueError:
            continue
    return None


def _parse_hours_range(value: Any) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Parse '10:00 am to 12:30 pm' into ((10,0), (12,30))."""
    text = str(value or '').strip()
    if not text:
        return None, None
    lowered = text.lower()
    sep = ' to ' if ' to ' in lowered else ('-' if '-' in text else None)
    if sep is None:
        return _parse_clock_time(text), None
    left, _, right = text.partition(' to ') if sep == ' to ' else text.partition('-')
    return _parse_clock_time(left), _parse_clock_time(right)


def _strip_html(value: Any) -> str:
    import re

    text = str(value or '')
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&laquo;', '').replace('&raquo;', '')
    return re.sub(r'\s+', ' ', text).strip()


def _appointment_location(appt: dict[str, Any]) -> str | None:
    return str(_dig_appointment(appt, 'location', 'pos', 'place_of_service', 'address') or '') or None


def _appointment_telehealth_link(appt: dict[str, Any]) -> str | None:
    """/ios/calendar's is_telehealth/telehealth_link fields on the
    data_all row shape — a separate join link, kept out of `location` so
    the frontend can render it as a distinct "Join" affordance."""
    if not _dig_appointment(appt, 'is_telehealth'):
        return None
    link = _dig_appointment(appt, 'telehealth_link')
    return str(link).strip() or None if link else None


def _appointment_service_name(appt: dict[str, Any]) -> str:
    direct = _dig_appointment(
        appt,
        'activity_type',
        'service_type',
        'activity_name',
        'authorization_activity_name',
        'service_hour',
        'service_name',  # /ios/calendar provider-filtered row shape (data_all)
    )
    if direct:
        return _strip_html(direct)

    service_list = _dig_appointment(appt, 'service_list', 'services')
    if isinstance(service_list, list) and service_list:
        joined = ', '.join(_strip_html(item) for item in service_list if item)
        if joined:
            return joined

    cpt = _dig_appointment(appt, 'cpt_code')
    return _strip_html(cpt) if cpt else ''


def _serialize_tpms_api_appointments(
    *,
    appointments: list[dict[str, Any]],
    dcm_client_id: int,
    status: str | None,
    from_date: date | None,
    to_date: date | None,
) -> list[dict[str, Any]]:
    from django.utils import timezone as tz
    from apps.sessions.models import Appointment as DcmAppointment

    def _aware(dt: datetime | None):
        if dt is None:
            return None
        return tz.make_aware(dt) if tz.is_naive(dt) else dt

    ext_ids = [
        str(_dig_appointment(
            appt, 'session_id', 'id', 'appointment_id', 'recurring_appointment_id',
            'recurring_session_id',
        ) or '')
        for appt in appointments
    ]
    ext_ids = [ext_id for ext_id in ext_ids if ext_id]
    dcm_by_ext: dict[str, DcmAppointment] = {}
    if ext_ids:
        for dcm in (
            DcmAppointment.objects
            .filter(external_id__in=ext_ids)
            .annotate(_program_count=Count('lesson__lesson_programs', distinct=True))
        ):
            dcm_by_ext[dcm.external_id] = dcm

    results: list[dict[str, Any]] = []
    for appt in appointments:
        raw_status = str(_dig_appointment(appt, 'status', 'appointment_status') or '')
        if raw_status.lower() in _TPMS_EXCLUDED_STATUSES:
            continue

        mapped_status = _tpms_status(raw_status)
        if status and mapped_status != status:
            continue

        start = _parse_appointment_datetime(
            _dig_appointment(
                appt,
                'from_time',
                'start_time',
                'appointment_start_time',
                'schedule_from',
                'start',  # /ios/calendar provider-filtered row shape (data_all)
            )
        )
        if start is None:
            schedule_date = _parse_appointment_date_only(
                _dig_appointment(
                    appt,
                    'start_date',
                    'schedule_date',
                    'scheduled_date',
                    'appointment_date',
                    'session_date',
                    'date',
                )
            )
            if schedule_date is not None:
                from_hm, to_hm = _parse_hours_range(_dig_appointment(appt, 'hours', 'time', 'scheduled_time'))
                if from_hm is not None:
                    start = datetime(
                        schedule_date.year, schedule_date.month, schedule_date.day,
                        from_hm[0], from_hm[1],
                    )
                else:
                    start = datetime(schedule_date.year, schedule_date.month, schedule_date.day)

        if start is None:
            continue

        end = _parse_appointment_datetime(
            _dig_appointment(appt, 'to_time', 'end_time', 'appointment_end_time', 'schedule_to', 'end')
        )
        if end is None:
            _, to_hm = _parse_hours_range(_dig_appointment(appt, 'hours', 'time', 'scheduled_time'))
            if to_hm is not None:
                end = datetime(start.year, start.month, start.day, to_hm[0], to_hm[1])
        if end is None:
            end = start

        if from_date and start.date() < from_date:
            continue
        if to_date and start.date() > to_date:
            continue

        duration_raw = _dig_appointment(appt, 'time_duration', 'duration_minutes', 'duration')
        duration_mins = 0
        if duration_raw not in (None, ''):
            try:
                duration_mins = int(duration_raw)
            except (TypeError, ValueError):
                duration_mins = 0
        if not duration_mins and end > start:
            duration_mins = int((end - start).total_seconds() / 60)

        ext_id = str(
            _dig_appointment(
                appt, 'session_id', 'id', 'appointment_id', 'recurring_appointment_id',
                'recurring_session_id',
            ) or ''
        )
        if not ext_id:
            continue

        provider_id = _dig_appointment(appt, 'provider_id', 'providerId', 'employee_id')
        try:
            staff_id = int(provider_id) if provider_id is not None else None
        except (TypeError, ValueError):
            staff_id = None

        service_name = _appointment_service_name(appt)
        dcm = dcm_by_ext.get(ext_id)
        results.append({
            'id': dcm.id if dcm else int(ext_id) if ext_id.isdigit() else 0,
            'client_id': dcm_client_id,
            'staff_id': staff_id,
            'staff_name': str(_dig_appointment(appt, 'provider_name', 'staff_name', 'employee_name') or '') or None,
            'lesson_id': dcm.lesson_id if dcm else None,
            'assigned_program_count': dcm._program_count if dcm else 0,
            'external_id': ext_id,
            'source': 'tpms',
            'start_time': _aware(start),
            'end_time': _aware(end),
            'service_type': service_name,
            'location': _appointment_location(appt),
            'telehealth_link': _appointment_telehealth_link(appt),
            'duration_minutes': duration_mins,
            'notes': _notes_text(_dig_appointment(appt, 'notes', 'note', 'comment')),
            'status': mapped_status,
            'synced_at': None,
            'created_at': _aware(
                _parse_appointment_datetime(_dig_appointment(appt, 'created_at'))
            ) or _aware(start),
        })

    results.sort(key=lambda row: row['start_time'], reverse=True)
    return results


def _list_native_client_sessions(
    request,
    client: Client,
    status: str | None,
    from_date: date | None,
    to_date: date | None,
):
    """Native (non-TPMS) equivalent of list_client_sessions — reads the local
    Appointment table directly (external_client_id holds the local Client.id by
    convention for native-mode appointments) instead of TpmsAppointment."""
    from apps.sessions.models import Appointment as DcmAppointment

    qs = DcmAppointment.objects.filter(external_client_id=client.id).annotate(
        assigned_program_count=Count('lesson__lesson_programs', distinct=True),
    )
    if request.user.role not in ('admin', 'supervisor'):
        qs = qs.filter(staff_id=request.user.id)
    if status:
        qs = qs.filter(status=status)
    if from_date:
        qs = qs.filter(start_time__date__gte=from_date)
    if to_date:
        qs = qs.filter(start_time__date__lte=to_date)
    return list(qs.order_by('-start_time'))


@router.get('/{client_id}/sessions', response=list[AppointmentSchema])
def list_client_sessions(
    request,
    client_id: int,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
):
    """
    Return appointments for a client from TherapyPMS iOS API.

    /clients rows are now TPMS providers (see list_providers in
    tpms_auth_client.py), so `Client.external_id` here is a provider id, not
    a patient id. Uses POST /api/v1/ios/calendar via list_provider_calendar
    with:
    - provider_ids: just this one client's TPMS provider id (`Client.external_id`)
    - start_date/end_date: from_date/to_date if given, else a wide default
      window, sent as YYYY-MM-DD

    Non-admin/supervisor staff only get results if this provider id is their
    own (`User.external_employee_id`) — otherwise they'd be looking at
    another provider's schedule under this client_id, which they shouldn't see.
    """
    client = _get_client_or_404(request, client_id)

    if not client.external_id:
        return _list_native_client_sessions(request, client, status, from_date, to_date)

    try:
        tpms_provider_id = int(client.external_id)
    except (TypeError, ValueError):
        raise HttpError(400, 'Client is missing a valid TherapyPMS provider id')

    token = get_tpms_access_token(request.user.id)
    if not token:
        raise HttpError(401, 'TherapyPMS session expired. Please log in again.')

    if request.user.role not in ('admin', 'supervisor'):
        if request.user.external_employee_id is None or int(request.user.external_employee_id) != tpms_provider_id:
            return []

    range_start = from_date or (date.today() - timedelta(days=3 * 365))
    range_end = to_date or (date.today() + timedelta(days=3 * 365))

    try:
        appointments = list_provider_calendar(
            token,
            provider_ids=[tpms_provider_id],
            start_date=range_start.isoformat(),
            end_date=range_end.isoformat(),
        )
    except TpmsAuthError as exc:
        if exc.status_code in {401, 403}:
            clear_tpms_access_token(request.user.id)
            raise HttpError(401, 'TherapyPMS session expired. Please log in again.') from exc
        raise HttpError(502, str(exc) or 'Failed to load appointments from TherapyPMS') from exc

    return _serialize_tpms_api_appointments(
        appointments=appointments,
        dcm_client_id=client_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
    )
