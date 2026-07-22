"""
TherapyPMS iOS auth + patient API client.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_TPMS_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days fallback
PATIENT_LIST_MAX_PAGES = 100
PATIENT_LIST_MAX_WORKERS = 8


class TpmsAuthError(Exception):
    """Raised when TherapyPMS auth fails or returns an unexpected payload."""

    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True)
class TpmsAuthProfile:
    """Normalized identity fields extracted from a successful TPMS login."""

    email: str
    first_name: str
    last_name: str
    external_admin_id: int | None
    external_employee_id: int | None
    is_admin: bool
    employee_type: str | None
    is_active: bool
    access_token: str | None
    raw: dict[str, Any]


def _base_url() -> str:
    return getattr(settings, 'TPMS_API_BASE_URL', 'https://app.therapypms.com').rstrip('/')


def _redis():
    import redis
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _tpms_token_key(user_id: int) -> str:
    return f'dcm:tpms:token:{user_id}'


def _normalize_bearer_token(token: str) -> str:
    value = (token or '').strip()
    if value.lower().startswith('bearer '):
        value = value[7:].strip()
    return value


def _authorization_header(token: str) -> str:
    raw = _normalize_bearer_token(token)
    return f'Bearer {raw}'


def _ttl_from_token(token: str) -> int:
    """Best-effort TTL from JWT exp; fall back to a long default."""
    try:
        import jwt as pyjwt

        payload = pyjwt.decode(
            _normalize_bearer_token(token),
            options={'verify_signature': False, 'verify_exp': False},
        )
        exp = payload.get('exp')
        if exp is not None:
            ttl = int(float(exp) - timezone.now().timestamp())
            if ttl > 60:
                return ttl
    except Exception:
        pass
    return DEFAULT_TPMS_TOKEN_TTL_SECONDS


def store_tpms_access_token(user_id: int, token: str) -> None:
    """Persist the TherapyPMS Bearer token for later API calls."""
    raw = _normalize_bearer_token(token)
    if not raw:
        return
    ttl = _ttl_from_token(raw)
    _redis().setex(_tpms_token_key(user_id), ttl, raw)


def get_tpms_access_token(user_id: int) -> str | None:
    value = _redis().get(_tpms_token_key(user_id))
    return value if isinstance(value, str) and value else None


def clear_tpms_access_token(user_id: int) -> None:
    _redis().delete(_tpms_token_key(user_id))


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    access_token: str | None = None,
    params: dict[str, Any] | None = None,
    debug_label: str = '',
) -> dict[str, Any]:
    url = f'{_base_url()}{path}'

    headers = {
        'Accept': 'application/json',
    }
    if body is not None:
        headers['Content-Type'] = 'application/json'
    if access_token:
        headers['Authorization'] = _authorization_header(access_token)

    try:
        session = requests.Session()
        session.trust_env = False
        response = session.request(
            method.upper(),
            url,
            json=body,
            params=params,
            headers=headers,
            timeout=getattr(settings, 'TPMS_API_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS),
        )
    except requests.RequestException as exc:
        logger.warning('TPMS request failed (%s %s): %s', method, path, exc)
        raise TpmsAuthError('TherapyPMS authentication service unavailable') from exc

    raw_text = response.text

    try:
        payload = response.json() if raw_text else {}
    except ValueError:
        raise TpmsAuthError(
            'TherapyPMS authentication returned an invalid response',
            status_code=response.status_code,
        )

    if response.status_code >= 400:
        message = 'TherapyPMS request failed'
        if isinstance(payload, dict):
            message = str(payload.get('message') or message)
        raise TpmsAuthError(message, status_code=response.status_code, payload=payload)

    if not isinstance(payload, dict):
        raise TpmsAuthError(
            'TherapyPMS authentication returned an invalid response',
            status_code=response.status_code,
            payload=payload,
        )
    return payload


def _post(path: str, body: dict[str, Any], *, debug_label: str = '') -> dict[str, Any]:
    return _request('POST', path, body=body, debug_label=debug_label)


def _cipher_from_encrypt_response(payload: dict[str, Any], field: str) -> str:
    """Pull ciphertext from an /ios/encrypt response."""
    value = payload.get('encrypted_data') or payload.get(field)
    if isinstance(value, str) and value:
        return value
    raise TpmsAuthError(
        f'TherapyPMS encrypt did not return encrypted {field}',
        payload=payload,
    )


def encrypt_credentials(email: str, password: str) -> tuple[str, str]:
    """
    Send plain email and password to /ios/encrypt (separate calls) and return
    the two ciphertexts for /ios/login.
    """
    email_payload = _post(
        '/api/v1/ios/encrypt',
        {'data': email},
        debug_label='encrypt-email',
    )
    password_payload = _post(
        '/api/v1/ios/encrypt',
        {'data': password},
        debug_label='encrypt-password',
    )

    enc_email = _cipher_from_encrypt_response(email_payload, 'email')
    enc_password = _cipher_from_encrypt_response(password_payload, 'password')
    return enc_email, enc_password


def login_with_encrypted(encrypted_email: str, encrypted_password: str) -> dict[str, Any]:
    """POST /ios/login with encrypt response values as email + password."""
    payload = _post(
        '/api/v1/ios/login',
        {'email': encrypted_email, 'password': encrypted_password},
        debug_label='login',
    )

    status = str(payload.get('status', '')).lower()
    if status in {'unauthorised', 'unauthorized', 'error', 'fail', 'failed'}:
        message = payload.get('message') or 'Invalid email or password'
        if isinstance(message, dict):
            message = next(iter(message.values()), ['Invalid email or password'])
            if isinstance(message, list):
                message = message[0] if message else 'Invalid email or password'
        raise TpmsAuthError(str(message), payload=payload)

    return payload


def authenticate(email: str, password: str) -> TpmsAuthProfile:
    """encrypt (email) + encrypt (password) → login → normalized profile."""
    enc_email, enc_password = encrypt_credentials(email, password)
    payload = login_with_encrypted(enc_email, enc_password)
    profile = normalize_login_payload(email, payload)
    return profile


def _practice_id_from_payload(payload: dict[str, Any]) -> int | None:
    """Best-effort practice/admin id from a TPMS JSON object or nested rows."""
    direct = _as_int(
        _dig(payload, 'admin_id', 'adminId', 'facility_id', 'facilityId', 'practice_id', 'up_admin_id')
    )
    if direct is not None:
        return direct

    for key in ('user', 'data', 'result', 'provider', 'employee', 'admin', 'patients'):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _practice_id_from_payload(nested)
            if found is not None:
                return found
        elif isinstance(nested, list):
            for item in nested[:10]:
                if isinstance(item, dict):
                    found = _practice_id_from_payload(item)
                    if found is not None:
                        return found
    return None


def resolve_practice_admin_id(access_token: str) -> int | None:
    """
    Provider /ios/login payloads omit practice id. Probe authenticated iOS
    endpoints that often carry admin_id / facility_id.
    """
    if not access_token:
        return None

    probe_paths = (
        ('/api/v1/ios/contact-info', 'contact-info'),
        ('/api/v1/ios/credentials', 'credentials'),
        ('/api/v1/ios/work-schedule', 'work-schedule'),
    )
    for path, label in probe_paths:
        try:
            payload = _request(
                'GET',
                path,
                access_token=access_token,
                debug_label=label,
            )
        except TpmsAuthError:
            continue
        found = _practice_id_from_payload(payload)
        if found is not None:
            return found

    try:
        _, rows, _ = _fetch_patient_list_page(access_token, 1)
    except TpmsAuthError:
        return None

    for row in rows[:20]:
        found = _practice_id_from_payload(row)
        if found is not None:
            return found
    return None


def _extract_rows(payload: dict[str, Any], *block_keys: str) -> list[dict[str, Any]]:
    """Flatten paginated or nested list payloads from TPMS iOS APIs."""
    rows: list[dict[str, Any]] = []

    def add_rows(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    rows.append(item)
        elif isinstance(value, dict):
            nested = value.get('data')
            if isinstance(nested, list):
                for item in nested:
                    if isinstance(item, dict):
                        rows.append(item)

    for key in block_keys:
        block = payload.get(key)
        if block is not None:
            add_rows(block)

    if not rows:
        data = payload.get('data')
        if isinstance(data, list):
            add_rows(data)
        elif isinstance(data, dict):
            add_rows(data)

    return rows


def _fetch_patient_list_page(
    access_token: str,
    page: int,
    *,
    search: str | None = None,
) -> tuple[int, list[dict[str, Any]], int]:
    """Fetch one patient-list page. Returns (page, rows, last_page)."""
    params: dict[str, Any] = {'page': page}
    if search:
        params['search'] = search

    payload = _request(
        'GET',
        '/api/v1/ios/patient/list',
        access_token=access_token,
        params=params,
        debug_label=f'patient-list-page-{page}',
    )

    status = str(payload.get('status', '')).lower()
    if status in {'unauthorised', 'unauthorized', 'error', 'fail', 'failed'}:
        message = payload.get('message') or 'Failed to load patients'
        raise TpmsAuthError(str(message), payload=payload)

    rows = _extract_rows(payload, 'patients')
    block = payload.get('patients')
    last_page = page
    if isinstance(block, dict):
        try:
            last_page = int(block.get('last_page') or page)
        except (TypeError, ValueError):
            last_page = page

    return page, rows, last_page


def list_patients(access_token: str, *, search: str | None = None) -> list[dict[str, Any]]:
    """
    Fetch all pages from GET /api/v1/ios/patient/list using the TPMS Bearer token.

    Page 1 is fetched first to learn `last_page`, then remaining pages are
    fetched in parallel so large practices load faster.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _, first_rows, last_page = _fetch_patient_list_page(access_token, 1, search=search)
    if last_page > PATIENT_LIST_MAX_PAGES:
        logger.warning(
            'TPMS patient list pagination exceeded %s pages (last_page=%s); capping',
            PATIENT_LIST_MAX_PAGES,
            last_page,
        )
    last_page = min(max(last_page, 1), PATIENT_LIST_MAX_PAGES)

    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}

    remaining = list(range(2, last_page + 1))
    if remaining:
        workers = min(PATIENT_LIST_MAX_WORKERS, len(remaining))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_patient_list_page, access_token, page, search=search): page
                for page in remaining
            }
            for future in as_completed(futures):
                page, rows, _ = future.result()
                pages[page] = rows

    patients: list[dict[str, Any]] = []
    for page in range(1, last_page + 1):
        patients.extend(pages.get(page, []))

    return patients


def list_recurring_appointments(
    access_token: str,
    *,
    patient_ids: list[int],
    provider_ids: list[int],
) -> list[dict[str, Any]]:
    """
    POST /api/v1/ios/appointment/recurring/list with TPMS patient + provider ids.
    """
    payload = _request(
        'POST',
        '/api/v1/ios/appointment/recurring/list',
        body={
            'patient_ids': patient_ids,
            'provider_ids': provider_ids,
        },
        access_token=access_token,
        debug_label='appointment-recurring-list',
    )

    status = str(payload.get('status', '')).lower()
    if status in {'unauthorised', 'unauthorized', 'error', 'fail', 'failed'}:
        message = payload.get('message') or 'Failed to load appointments'
        raise TpmsAuthError(str(message), payload=payload)

    appointments = _extract_rows(
        payload,
        'recurring_sessions',
        'appointments',
        'recurring_appointments',
        'appointment_list',
        'data',
    )
    return appointments


def _looks_like_profile(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    keys = {k.lower() for k in value.keys()}
    identity_keys = {
        'email', 'login_email', 'id', 'admin_id', 'employee_id',
        'first_name', 'last_name', 'name', 'full_name', 'user', 'token', 'access_token',
        'account_type',
    }
    return bool(keys & identity_keys)


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


def _unwrap_profile(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ('data', 'user', 'result', 'admin', 'employee', 'provider'):
        nested = payload.get(key)
        if isinstance(nested, dict) and _looks_like_profile(nested):
            return {**payload, **nested}
    return payload


def _extract_access_token(payload: dict[str, Any]) -> str | None:
    data = _unwrap_profile(payload)
    raw = _dig(data, 'access_token', 'token', 'bearer_token', 'accessToken')
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _normalize_bearer_token(raw)


def normalize_login_payload(fallback_email: str, payload: dict[str, Any]) -> TpmsAuthProfile:
    data = _unwrap_profile(payload)

    email = str(
        _dig(data, 'email', 'login_email', 'office_email') or fallback_email
    ).strip().lower()

    full_name = str(_dig(data, 'full_name', 'name') or '').strip()
    first_name = str(
        _dig(data, 'first_name', 'firstname', 'fname')
        or (full_name.split(' ')[0] if full_name else '')
        or ''
    )
    last_name = str(
        _dig(data, 'last_name', 'lastname', 'lname')
        or (' '.join(full_name.split(' ')[1:]) if full_name else '')
        or ''
    )

    account_type = str(_dig(data, 'account_type', 'accountType') or '').lower()
    user_type = str(
        _dig(data, 'user_type', 'userType', 'type', 'role', 'login_type', 'employee_type')
        or account_type
        or ''
    ).lower()
    employee_type = _dig(data, 'employee_type', 'employeeType')
    if employee_type is not None:
        employee_type = str(employee_type)

    is_admin = (
        account_type in {'admin', 'facility', 'owner', 'practice'}
        or any(token in user_type for token in ('admin', 'facility', 'owner', 'practice'))
        or bool(_dig(data, 'is_admin', 'isAdmin'))
    )
    is_provider = account_type in {'provider', 'employee', 'staff'} or 'provider' in user_type

    is_up_admin = _dig(data, 'is_up_admin', 'isUpAdmin')
    own_id = _as_int(_dig(data, 'id', 'user_id', 'userId'))
    admin_id = _as_int(_dig(data, 'admin_id', 'adminId', 'facility_id', 'facilityId', 'practice_id'))
    up_admin_id = _as_int(_dig(data, 'up_admin_id', 'upAdminId'))

    if is_admin and not is_provider:
        if is_up_admin in (1, '1', True, 'true') or (is_up_admin is None and up_admin_id is None):
            external_admin_id = admin_id or own_id
        else:
            external_admin_id = up_admin_id or admin_id or own_id
        external_employee_id = _as_int(
            _dig(data, 'employee_id', 'employeeId', 'provider_id', 'providerId')
        )
    else:
        external_admin_id = admin_id
        external_employee_id = _as_int(
            _dig(data, 'employee_id', 'employeeId', 'provider_id', 'providerId', 'id', 'user_id')
        )
        is_admin = False

    active_raw = _dig(data, 'active', 'is_active', 'isActive', 'is_staff_active', 'account_status')
    if active_raw is None:
        is_active = True
    else:
        is_active = str(active_raw).lower() not in {'0', 'false', 'inactive', 'disabled'}

    if _dig(data, 'is_supervisor', 'isSupervisor') in (True, 1, '1', 'true'):
        employee_type = employee_type or 'supervisor'

    return TpmsAuthProfile(
        email=email,
        first_name=first_name,
        last_name=last_name,
        external_admin_id=external_admin_id,
        external_employee_id=external_employee_id,
        is_admin=is_admin,
        employee_type=employee_type or (user_type or account_type or None),
        is_active=is_active,
        access_token=_extract_access_token(payload),
        raw=payload,
    )
