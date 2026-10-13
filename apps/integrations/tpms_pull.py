"""
TherapyPMS → Progressly Integrations Pull sync.

Called from Organization → Integrations after a successful Connect.
Uses the practice-admin APIs (/api/v1/admin/get/*) and upserts into the
current Organization only, tagged with the connected practice's
`external_admin_id` so a multi-facility deployment cannot leak rows
across practices or organizations.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.clients.models import Client
from apps.integrations.models import Provider
from apps.integrations.tpms_auth_client import (
    TpmsAuthError,
    admin_list_appointments,
    admin_list_clients,
    admin_list_providers,
    authenticate_admin_raw,
    clear_org_tpms_admin_token,
    get_org_tpms_admin_token,
    normalize_admin_login_payload,
    store_org_tpms_admin_token,
)
from apps.integrations.tpms_credentials import decrypt_password, decrypt_ssn, encrypt_password, encrypt_ssn
from apps.sessions.models import Appointment
from apps.tenants.models import Organization
from shared.tenancy import current_org_id_or_none

logger = logging.getLogger(__name__)


@dataclass
class PullResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            'created': self.created,
            'updated': self.updated,
            'skipped': self.skipped,
            'errors': self.errors[:20],
        }


def _as_int(value: Any) -> int | None:
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dig(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ''):
            return data[key]
        lower = key.lower()
        for existing, value in data.items():
            if existing.lower() == lower and value not in (None, ''):
                return value
    return None


def _row_admin_id(row: dict[str, Any]) -> int | None:
    return _as_int(_dig(row, 'admin_id', 'adminId', 'up_admin_id', 'facility_id', 'practice_id'))


def _belongs_to_practice(row: dict[str, Any], practice_admin_id: int) -> bool:
    """Rows without an admin_id are assumed practice-scoped by the admin token.

    When an admin_id IS present it must match the connected practice — this is
    the belt-and-suspenders guard against a token that somehow returns
    another facility's data.
    """
    row_admin = _row_admin_id(row)
    if row_admin is None:
        return True
    return row_admin == practice_admin_id


def _tpms_role_for_employee_type(employee_type: str | None, *, is_admin: bool = False) -> str:
    if is_admin:
        return User.Role.ADMIN
    if not employee_type:
        return User.Role.STAFF
    et = employee_type.lower()
    if 'bcba' in et or 'supervisor' in et or 'admin' in et:
        return User.Role.SUPERVISOR
    return User.Role.STAFF


def _tpms_appointment_status(raw: str | None) -> str:
    s = (raw or '').lower()
    if s in ('rendered', 'completed', 'kept'):
        return Appointment.Status.COMPLETED
    if s in ('cancelled', 'canceled'):
        return Appointment.Status.CANCELLED
    if s in ('no show', 'no-show', 'noshow'):
        return Appointment.Status.NO_SHOW
    return Appointment.Status.SCHEDULED


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        pass
    for fmt in (
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
        '%m/%d/%Y %H:%M:%S',
        '%m/%d/%Y %H:%M',
        '%m/%d/%Y',
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _split_name(full: str) -> tuple[str, str]:
    name = (full or '').strip()
    if not name:
        return 'Unknown', 'Unknown'
    if ' ' in name:
        first, last = name.split(' ', 1)
        return first.strip() or 'Unknown', last.strip() or 'Unknown'
    return '', name


def _require_connected_org(org: Organization) -> None:
    if org.integration_platform != Organization.IntegrationPlatform.THERAPY_PMS:
        raise HttpError(400, 'Connect TherapyPMS before pulling data.')
    if not org.integration_email or not org.integration_password_encrypted:
        raise HttpError(400, 'TherapyPMS credentials are missing. Please reconnect.')
    if org.integration_admin_id is None:
        raise HttpError(400, 'TherapyPMS practice binding is missing. Please reconnect.')


def _org_practice_ids(org: Organization) -> set[int]:
    return set(org.tpms_admin_ids.values_list('admin_id', flat=True))


def bind_therapy_pms_connection(org: Organization, email: str, password: str) -> int:
    """Verify admin credentials, bind to a mapped practice, store encrypted secrets.

    Returns the bound TPMS admin_id.
    """
    try:
        payload = authenticate_admin_raw(email, password)
        session = normalize_admin_login_payload(email, payload)
    except TpmsAuthError as exc:
        raise HttpError(
            400,
            'Error making a request to TherapyPMS. Please verify that the credentials are valid and try again. '
            'If the problem persists, please contact TherapyPMS support.',
        ) from exc

    mapped = _org_practice_ids(org)
    if not mapped:
        raise HttpError(
            400,
            'This organization has no TherapyPMS practice mapped yet. '
            'Ask a Progressly superadmin to add the practice admin id before connecting.',
        )
    if session.admin_id not in mapped:
        raise HttpError(
            400,
            'These TherapyPMS credentials belong to a practice that is not mapped to this organization. '
            'Ask a Progressly superadmin to map the correct practice admin id.',
        )

    org.integration_platform = Organization.IntegrationPlatform.THERAPY_PMS
    org.integration_email = email.strip().lower()
    org.integration_password_encrypted = encrypt_password(password)
    org.integration_admin_id = session.admin_id
    org.save(
        update_fields=[
            'integration_platform',
            'integration_email',
            'integration_password_encrypted',
            'integration_admin_id',
            'updated_at',
        ],
    )
    store_org_tpms_admin_token(org.id, session.access_token)
    return session.admin_id


def clear_therapy_pms_connection(org: Organization) -> None:
    clear_org_tpms_admin_token(org.id)
    org.integration_platform = ''
    org.integration_email = ''
    org.integration_password_encrypted = ''
    org.integration_admin_id = None
    org.save(
        update_fields=[
            'integration_platform',
            'integration_email',
            'integration_password_encrypted',
            'integration_admin_id',
            'updated_at',
        ],
    )


def _admin_access_token(org: Organization) -> str:
    """Return a usable admin Bearer token, refreshing via stored credentials if needed."""
    _require_connected_org(org)
    cached = get_org_tpms_admin_token(org.id)
    if cached:
        return cached

    password = decrypt_password(org.integration_password_encrypted)
    try:
        payload = authenticate_admin_raw(org.integration_email, password)
        session = normalize_admin_login_payload(org.integration_email, payload)
    except TpmsAuthError as exc:
        clear_org_tpms_admin_token(org.id)
        raise HttpError(
            400,
            'Could not refresh TherapyPMS credentials. Please reconnect.',
        ) from exc

    if session.admin_id != org.integration_admin_id:
        # Credential now resolves to a different practice — refuse rather than
        # silently sync the wrong facility into this org.
        raise HttpError(
            400,
            'TherapyPMS credentials now resolve to a different practice. Please reconnect.',
        )

    store_org_tpms_admin_token(org.id, session.access_token)
    return session.access_token


def _with_admin_token(org: Organization, call):
    token = _admin_access_token(org)
    try:
        return call(token)
    except TpmsAuthError as exc:
        if exc.status_code in {401, 403}:
            clear_org_tpms_admin_token(org.id)
            token = _admin_access_token(org)
            try:
                return call(token)
            except TpmsAuthError as retry_exc:
                raise HttpError(502, str(retry_exc) or 'TherapyPMS request failed') from retry_exc
        raise HttpError(502, str(exc) or 'TherapyPMS request failed') from exc


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def _map_admin_client(row: dict[str, Any], *, practice_admin_id: int) -> dict[str, Any] | None:
    ext_id = _as_int(_dig(row, 'id', 'client_id', 'patient_id', 'clientId', 'patientId'))
    if ext_id is None:
        return None

    first = str(
        _dig(row, 'client_first_name', 'first_name', 'firstname', 'fname') or ''
    ).strip()
    last = str(
        _dig(row, 'client_last_name', 'last_name', 'lastname', 'lname') or ''
    ).strip()
    preferred = str(
        _dig(row, 'client_preferred', 'preferred_name', 'preferred') or ''
    ).strip()
    if not first and not last:
        first, last = _split_name(
            str(_dig(row, 'client_full_name', 'full_name', 'name', 'client_name') or '')
        )

    dob = None
    raw_dob = _dig(row, 'client_dob', 'date_of_birth', 'dob', 'birth_date')
    parsed_dob = _parse_datetime(raw_dob)
    if parsed_dob is not None:
        dob = parsed_dob.date()

    active_raw = _dig(row, 'is_active_client', 'is_active', 'active', 'status')
    if active_raw is None:
        status = Client.Status.ACTIVE
    else:
        inactive = str(active_raw).lower() in {'0', 'false', 'inactive', 'discharged', 'on_hold', '2'}
        status = Client.Status.INACTIVE if inactive else Client.Status.ACTIVE

    return {
        'external_id': str(ext_id),
        'external_admin_id': practice_admin_id,
        'first_name': first or 'Unknown',
        'last_name': last or 'Unknown',
        'preferred_name': preferred,
        'date_of_birth': dob,
        'status': status,
    }


def pull_clients(org: Organization) -> PullResult:
    result = PullResult()
    practice_admin_id = org.integration_admin_id
    assert practice_admin_id is not None

    rows = _with_admin_token(org, admin_list_clients)
    org_id = current_org_id_or_none() or org.id

    mapped: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            result.skipped += 1
            continue
        if not _belongs_to_practice(row, practice_admin_id):
            result.skipped += 1
            continue
        fields = _map_admin_client(row, practice_admin_id=practice_admin_id)
        if fields is None:
            result.skipped += 1
            continue
        mapped.append(fields)

    if not mapped:
        return result

    ext_ids = [m['external_id'] for m in mapped]
    existing = {
        c.external_id: c
        for c in Client.objects.filter(
            external_id__in=ext_ids,
            external_admin_id=practice_admin_id,
        )
    }

    update_attrs = (
        'first_name', 'last_name', 'preferred_name', 'date_of_birth',
        'status', 'external_admin_id',
    )
    to_create: list[Client] = []
    to_update: list[Client] = []

    for fields in mapped:
        ext_id = fields['external_id']
        current = existing.get(ext_id)
        if current is None:
            client = Client(**fields)
            client.organization_id = org_id
            to_create.append(client)
            existing[ext_id] = client
            result.created += 1
        else:
            # Never move a client that somehow belongs to another practice.
            if current.external_admin_id not in (None, practice_admin_id):
                result.skipped += 1
                continue
            changed = False
            for attr in update_attrs:
                value = fields.get(attr)
                if getattr(current, attr) != value:
                    setattr(current, attr, value)
                    changed = True
            if changed:
                to_update.append(current)
                result.updated += 1
            else:
                result.skipped += 1

    with transaction.atomic():
        if to_create:
            Client.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            Client.objects.bulk_update(to_update, list(update_attrs), batch_size=200)

    return result


# ---------------------------------------------------------------------------
# Providers → User
# ---------------------------------------------------------------------------

def _map_admin_provider(row: dict[str, Any], *, practice_admin_id: int, org_id: int) -> dict[str, Any] | None:
    employee_id = _as_int(
        _dig(row, 'id', 'employee_id', 'provider_id', 'staff_id', 'employeeId', 'providerId')
    )
    if employee_id is None:
        return None

    email = str(
        _dig(row, 'login_email', 'email', 'office_email', 'user_email') or ''
    ).strip().lower()
    if not email:
        email = f'tpms.employee.{employee_id}.org{org_id}@users.progressly.invalid'

    first = str(_dig(row, 'first_name', 'firstname', 'fname') or '').strip()
    # 'last_lane' — the real /admin/get/providers payload has this typo'd key.
    last = str(_dig(row, 'last_name', 'last_lane', 'lastname', 'lname') or '').strip()
    if not first and not last:
        first, last = _split_name(str(_dig(row, 'full_name', 'name', 'provider_name') or ''))

    employee_type = _dig(row, 'employee_type', 'employeeType', 'role', 'type', 'user_type')
    employee_type = str(employee_type) if employee_type is not None else None
    is_admin = str(_dig(row, 'is_admin', 'isAdmin') or '').lower() in {'1', 'true', 'yes'}
    role = _tpms_role_for_employee_type(employee_type, is_admin=is_admin)

    active_raw = _dig(row, 'is_active', 'is_staff_active', 'active', 'account_status')
    if active_raw is None:
        is_active = True
    else:
        is_active = str(active_raw).lower() not in {'0', 'false', 'inactive', 'disabled'}

    middle_name = str(_dig(row, 'middle_name', 'middlename') or '').strip()
    birthday_raw = _dig(row, 'staff_birthday', 'date_of_birth', 'birthday', 'dob')
    birthday_dt = _parse_datetime(birthday_raw)
    # A birthdate is a calendar date, not a timezone-anchored instant — take the
    # date as sent, since converting to local time could shift it a day either way.
    date_of_birth = birthday_dt.date() if birthday_dt else None
    ssn = str(_dig(row, 'ssn') or '').strip()
    other_id = str(_dig(row, 'staff_other_id', 'other_id') or '').strip()
    office_phone = str(_dig(row, 'office_phone', 'phone') or '').strip()
    office_fax = str(_dig(row, 'office_fax', 'fax') or '').strip()

    return {
        'email': email,
        'first_name': first or 'Unknown',
        'middle_name': middle_name,
        'last_name': last or 'Unknown',
        'date_of_birth': date_of_birth,
        'ssn': ssn,
        'external_other_id': other_id,
        'office_phone': office_phone,
        'office_fax': office_fax,
        'role': role,
        'employee_type': employee_type or '',
        'is_active': is_active,
        'external_admin_id': practice_admin_id,
        'external_employee_id': employee_id,
    }


def pull_providers(org: Organization) -> PullResult:
    result = PullResult()
    practice_admin_id = org.integration_admin_id
    assert practice_admin_id is not None
    org_id = current_org_id_or_none() or org.id

    rows = _with_admin_token(org, admin_list_providers)

    for row in rows:
        if not isinstance(row, dict):
            result.skipped += 1
            continue
        if not _belongs_to_practice(row, practice_admin_id):
            result.skipped += 1
            continue
        fields = _map_admin_provider(row, practice_admin_id=practice_admin_id, org_id=org_id)
        if fields is None:
            result.skipped += 1
            continue

        email = fields['email']
        employee_id = fields['external_employee_id']

        # Cross-org / cross-practice leak guards — User.email is globally unique.
        by_email = User.objects.filter(email__iexact=email).first()
        if by_email is not None:
            if by_email.organization_id not in (None, org_id):
                result.skipped += 1
                result.errors.append(f'Skipped provider {employee_id}: email owned by another organization')
                continue
            if by_email.role == User.Role.CAREGIVER:
                result.skipped += 1
                result.errors.append(f'Skipped provider {employee_id}: email is a caregiver account')
                continue
            if by_email.external_admin_id not in (None, practice_admin_id):
                result.skipped += 1
                result.errors.append(f'Skipped provider {employee_id}: email bound to another practice')
                continue

        by_employee = (
            User.objects
            .filter(external_employee_id=employee_id, organization_id=org_id)
            .exclude(role=User.Role.CAREGIVER)
            .first()
        )
        if by_employee is not None and by_employee.external_admin_id not in (None, practice_admin_id):
            result.skipped += 1
            continue

        user = by_employee or by_email
        with transaction.atomic():
            user_created = user is None
            if user is None:
                user = User(
                    email=email,
                    first_name=fields['first_name'],
                    last_name=fields['last_name'],
                    role=fields['role'],
                    is_active=fields['is_active'],
                    external_admin_id=practice_admin_id,
                    external_employee_id=employee_id,
                    organization_id=org_id,
                )
                user.set_unusable_password()
                user.save()
                user_changed = True
            else:
                update_fields: list[str] = []
                for attr in ('first_name', 'last_name', 'role', 'is_active'):
                    if getattr(user, attr) != fields[attr]:
                        setattr(user, attr, fields[attr])
                        update_fields.append(attr)
                if user.external_admin_id != practice_admin_id:
                    user.external_admin_id = practice_admin_id
                    update_fields.append('external_admin_id')
                if user.external_employee_id != employee_id:
                    user.external_employee_id = employee_id
                    update_fields.append('external_employee_id')
                if user.organization_id != org_id:
                    user.organization_id = org_id
                    update_fields.append('organization')
                # Prefer real TPMS email over synthetic placeholder when available.
                if (
                    user.email.lower() != email
                    and email.endswith('@users.progressly.invalid') is False
                    and by_email is None
                ):
                    user.email = email
                    update_fields.append('email')
                if update_fields:
                    user.save(update_fields=update_fields)
                user_changed = bool(update_fields)

            # Separate Provider directory record — kept independently of the
            # User account above (see Provider's docstring for why).
            provider = Provider.objects.filter(
                organization_id=org_id,
                external_admin_id=practice_admin_id,
                external_employee_id=employee_id,
            ).first()
            provider_created = provider is None
            provider_changed = provider_created
            if provider is None:
                provider = Provider(
                    organization_id=org_id,
                    external_admin_id=practice_admin_id,
                    external_employee_id=employee_id,
                )
            for attr in (
                'email', 'first_name', 'middle_name', 'last_name', 'date_of_birth',
                'external_other_id', 'office_phone', 'office_fax', 'role', 'employee_type', 'is_active',
            ):
                if getattr(provider, attr) != fields[attr]:
                    setattr(provider, attr, fields[attr])
                    provider_changed = True
            # SSN is encrypted at rest; Fernet ciphertext differs every time even for
            # the same plaintext, so compare decrypted values rather than ciphertext.
            if decrypt_ssn(provider.ssn_encrypted) != fields['ssn']:
                provider.ssn_encrypted = encrypt_ssn(fields['ssn']) if fields['ssn'] else ''
                provider_changed = True
            if provider.user_id != user.id:
                provider.user = user
                provider_changed = True
            if provider_changed:
                provider.save()

            if user_created or provider_created:
                result.created += 1
            elif user_changed or provider_changed:
                result.updated += 1
            else:
                result.skipped += 1

    return result


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

def _map_admin_appointment(
    row: dict[str, Any],
    *,
    practice_admin_id: int,
    staff_by_employee: dict[int, User],
) -> dict[str, Any] | None:
    ext_id = _as_int(
        _dig(row, 'id', 'appointment_id', 'session_id', 'appointmentId', 'sessionId')
    )
    if ext_id is None:
        return None

    patient_id = _as_int(
        _dig(row, 'client_id', 'patient_id', 'clientId', 'patientId')
    )
    provider_id = _as_int(
        _dig(row, 'provider_id', 'staff_id', 'employee_id', 'providerId', 'staffId')
    )

    start = _parse_datetime(
        _dig(row, 'from_time', 'start_time', 'start', 'schedule_from', 'appointment_start_time')
    )
    end = _parse_datetime(
        _dig(row, 'to_time', 'end_time', 'end', 'schedule_to', 'appointment_end_time')
    )
    if start is None:
        schedule_date = _parse_datetime(
            _dig(row, 'schedule_date', 'scheduled_date', 'date', 'appointment_date')
        )
        if schedule_date is not None:
            start = schedule_date
    if start is None:
        return None
    if end is None:
        end = start

    start = _aware(start)
    end = _aware(end)
    if start is None or end is None:
        return None

    service = str(
        _dig(
            row,
            'activity_type',
            'service_type',
            'service_name',
            'activity_name',
            'authorization_activity_name',
            'cpt_code',
        )
        or ''
    )[:100]
    notes = str(_dig(row, 'notes', 'note', 'location', 'address') or '')
    status = _tpms_appointment_status(str(_dig(row, 'status', 'appointment_status') or ''))

    staff = staff_by_employee.get(provider_id) if provider_id is not None else None

    session_title = str(_dig(row, 'session_title') or '')[:255]
    row_admin_id = _row_admin_id(row)
    billable = _as_int(_dig(row, 'billable'))
    authorization_id = str(_dig(row, 'authorization_id') or '')[:100]
    payor_id = _as_int(_dig(row, 'payor_id'))
    time_duration = _as_int(_dig(row, 'time_duration', 'duration'))
    if time_duration is not None and time_duration < 0:
        time_duration = None
    cpt_code = str(_dig(row, 'cpt_code') or '')[:20]

    external_created_at = _aware(_parse_datetime(_dig(row, 'created_at')))
    external_updated_at = _aware(_parse_datetime(_dig(row, 'updated_at')))
    start_time_local_raw = str(_dig(row, 'from_time_timezone') or '')[:40]
    end_time_local_raw = str(_dig(row, 'to_time_timezone') or '')[:40]

    return {
        'external_id': str(ext_id),
        'external_client_id': patient_id,
        'staff_id': staff.id if staff is not None else None,
        'start_time': start,
        'end_time': end,
        'service_type': service,
        'notes': notes,
        'status': status,
        'source': Appointment.Source.SYNCED,
        'synced_at': timezone.now(),
        'session_title': session_title,
        'external_admin_id': row_admin_id if row_admin_id is not None else practice_admin_id,
        'billable': billable,
        'authorization_id': authorization_id,
        'payor_id': payor_id,
        'time_duration': time_duration,
        'cpt_code': cpt_code,
        'external_created_at': external_created_at,
        'external_updated_at': external_updated_at,
        'start_time_local_raw': start_time_local_raw,
        'end_time_local_raw': end_time_local_raw,
    }


def _month_windows(from_date: date, to_date: date) -> list[tuple[date, date]]:
    """Splits [from_date, to_date] into calendar-month windows (inclusive),
    so a multi-year pull is many small TPMS requests instead of one
    unbounded one that risks both TPMS's own response time and the web
    server's request timeout."""
    windows: list[tuple[date, date]] = []
    cur = from_date
    while cur <= to_date:
        next_month_start = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
        window_end = min(to_date, next_month_start - timedelta(days=1))
        windows.append((cur, window_end))
        cur = next_month_start
    return windows


def pull_appointments(
    org: Organization,
    *,
    from_date: date,
    to_date: date,
    patient_ids: list[int] | None = None,
    staff_ids: list[int] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> PullResult:
    """Pulls appointments for [from_date, to_date] one calendar month at a
    time via _pull_appointments_window, aggregating the results. Chunking
    makes a years-long backfill tractable (each TPMS request covers at most
    one month) and gives on_progress something meaningful to report when
    this runs as a long Celery task."""
    if to_date < from_date:
        raise HttpError(400, 'to_date must be on or after from_date')

    windows = _month_windows(from_date, to_date)
    total = PullResult()
    for i, (window_start, window_end) in enumerate(windows, start=1):
        chunk = _pull_appointments_window(
            org,
            from_date=window_start,
            to_date=window_end,
            patient_ids=patient_ids,
            staff_ids=staff_ids,
        )
        total.created += chunk.created
        total.updated += chunk.updated
        total.skipped += chunk.skipped
        total.errors.extend(chunk.errors)
        if on_progress:
            on_progress(i, len(windows))
    return total


def _pull_appointments_window(
    org: Organization,
    *,
    from_date: date,
    to_date: date,
    patient_ids: list[int] | None = None,
    staff_ids: list[int] | None = None,
) -> PullResult:
    result = PullResult()
    practice_admin_id = org.integration_admin_id
    assert practice_admin_id is not None
    org_id = current_org_id_or_none() or org.id

    if to_date < from_date:
        raise HttpError(400, 'to_date must be on or after from_date')

    def _fetch(token: str):
        return admin_list_appointments(
            token,
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
            patient_ids=patient_ids,
            staff_ids=staff_ids,
        )

    rows = _with_admin_token(org, _fetch)

    # Only link staff that belong to this org + practice.
    staff_by_employee = {
        u.external_employee_id: u
        for u in User.objects.filter(
            organization_id=org_id,
            external_admin_id=practice_admin_id,
            external_employee_id__isnull=False,
        ).exclude(role=User.Role.CAREGIVER)
        if u.external_employee_id is not None
    }

    # Only accept appointments whose patient already exists in this practice —
    # never invent a client from an appointment alone (avoids orphan leakage).
    known_patient_ids = set(
        Client.objects.filter(external_admin_id=practice_admin_id)
        .exclude(external_id='')
        .values_list('external_id', flat=True)
    )

    mapped: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            result.skipped += 1
            continue
        if not _belongs_to_practice(row, practice_admin_id):
            result.skipped += 1
            continue
        fields = _map_admin_appointment(
            row,
            practice_admin_id=practice_admin_id,
            staff_by_employee=staff_by_employee,
        )
        if fields is None:
            result.skipped += 1
            continue
        patient_id = fields.get('external_client_id')
        if patient_id is not None and str(patient_id) not in known_patient_ids:
            # Still store the appointment with the TPMS patient id — programs
            # resolve via external_client_id. Client pull should be run first
            # for a complete graph; we do not skip solely for missing Client.
            pass
        mapped.append(fields)

    if not mapped:
        return result

    ext_ids = [m['external_id'] for m in mapped]
    existing = {
        a.external_id: a
        for a in Appointment.objects.filter(external_id__in=ext_ids, source=Appointment.Source.SYNCED)
    }

    update_attrs = (
        'external_client_id', 'staff_id', 'start_time', 'end_time',
        'service_type', 'notes', 'status', 'synced_at',
        'session_title', 'external_admin_id', 'billable', 'authorization_id',
        'payor_id', 'time_duration', 'cpt_code', 'external_created_at',
        'external_updated_at', 'start_time_local_raw', 'end_time_local_raw',
    )
    to_create: list[Appointment] = []
    to_update: list[Appointment] = []

    for fields in mapped:
        ext_id = fields['external_id']
        current = existing.get(ext_id)
        if current is None:
            appt = Appointment(**fields)
            appt.organization_id = org_id
            to_create.append(appt)
            existing[ext_id] = appt
            result.created += 1
        else:
            # Synced appointments are org-scoped via TenantManager; still refuse
            # to overwrite a manual appointment with a conflicting external_id.
            if current.source != Appointment.Source.SYNCED:
                result.skipped += 1
                continue
            changed = False
            for attr in update_attrs:
                value = fields.get(attr)
                if getattr(current, attr) != value:
                    setattr(current, attr, value)
                    changed = True
            if changed:
                to_update.append(current)
                result.updated += 1
            else:
                result.skipped += 1

    with transaction.atomic():
        if to_create:
            Appointment.objects.bulk_create(to_create, batch_size=200)
        if to_update:
            Appointment.objects.bulk_update(
                to_update,
                list(update_attrs),
                batch_size=200,
            )

    return result
