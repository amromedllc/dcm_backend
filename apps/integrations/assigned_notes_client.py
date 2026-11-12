"""Server-only transport for the TPMS assigned-session-note contract.

The custom-page request uses the agreed old_form_builder source and diagnostic
flag. Only validated note IDs, names, and opening URLs are exposed to the UI.
"""
import logging

from datetime import datetime, timezone
from uuid import uuid4
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

import jwt
import requests
from django.conf import settings


logger = logging.getLogger(__name__)


class AssignedNotesError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _sign_token(employee_id: int, shared_secret: str) -> str:
    if type(employee_id) is not int or employee_id <= 0:
        raise AssignedNotesError('An employee-linked TherapyPMS account is required for Old Form Builder.', 403)
    if not shared_secret:
        raise AssignedNotesError('Old Form Builder is not configured. Please contact an administrator.', 503)
    now = int(datetime.now(timezone.utc).timestamp())
    return jwt.encode({
        'tpms_user_id': employee_id,
        'aud': 'tpms-progressly',
        'jti': str(uuid4()),
        'iat': now,
        'exp': now + 300,
    }, shared_secret, algorithm='HS256')


def fetch_assigned_session_notes(user, session_id: int = 0):
    """Fetch the custom-page list with a fresh token per call.

    session_id=0 lists the employee's assigned forms generically (used by the
    Templates page, with no session in view). Called from a specific session,
    pass that session's TPMS appointment id (SessionRun.external_appointment_id)
    instead, so the url TPMS returns for each note is tied to that appointment
    rather than opening a generic/untied form.

    Never retry a request with the same single-use token. Never return or log the
    request token, shared secret, or unfiltered upstream errors to the browser.
    """
    if type(session_id) is not int or session_id < 0:
        raise AssignedNotesError('Invalid session reference for Old Form Builder.', 400)
    token = _sign_token(
        getattr(user, 'external_employee_id', None),
        getattr(settings, 'TPMS_PROGRESSLY_SHARED_SECRET', ''),
    )
    url = settings.TPMS_API_BASE_URL.rstrip('/') + '/api/v1/ios/progressly/sn-assigned-list'
    try:
        response = requests.post(
            url,
            headers={'Accept': 'application/json'},
            json={'token': token, 'session_id': session_id, 'source_type': 'old_form_builder', 'is_diagnostic': 'Yes'},
            timeout=settings.TPMS_API_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.Timeout:
        logger.warning('old_form_builder request_failed reason=timeout')
        raise AssignedNotesError('TherapyPMS took too long to respond. Please refresh forms and try again.') from None
    except requests.RequestException:
        logger.warning('old_form_builder request_failed reason=connection')
        raise AssignedNotesError('Old Form Builder could not be reached. Please try again.') from None
    if not 200 <= response.status_code < 300:
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = None
        reason = _known_error_reason(error_payload)
        logger.warning('old_form_builder upstream_request_failed status=%d reason=%s',
                       response.status_code, reason or 'unrecognized')
        raise AssignedNotesError(_upstream_error_message(response.status_code, reason))
    try:
        payload = response.json()
    except ValueError:
        raise AssignedNotesError('Old Form Builder returned an invalid response.') from None
    return _parse_notes(payload)


def _known_error_reason(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    messages = {
        'Invalid or expired token': 'invalid_or_expired_token',
        'Token has already been used': 'token_already_used',
        'Employee not found': 'employee_not_found',
    }
    for key in ('message', 'error', 'detail'):
        value = payload.get(key)
        if isinstance(value, str) and value.strip() in messages:
            return messages[value.strip()]
    return None


def _upstream_error_message(status: int, reason: str | None = None) -> str:
    prefix = f'TherapyPMS returned HTTP {status}. '
    if reason == 'invalid_or_expired_token':
        return prefix + ('Invalid or expired token. The Progressly shared signing secret must match '
                         'TherapyPMS services.progressly.sso_secret exactly. Ask your administrator '
                         'to verify the shared secret and server clocks.')
    if reason == 'token_already_used':
        return prefix + 'Token has already been used. Refresh forms to generate a fresh signed token and request ID.'
    if reason == 'employee_not_found':
        return prefix + ('Employee not found. Ask your administrator to verify this account’s TherapyPMS '
                         'employee ID and that the employee’s facility has setting_api_type.data_collection = 4 in TherapyPMS.')
    if status in (401, 403):
        return prefix + ('Old Form Builder authorization was rejected. Ask your administrator to verify '
                         'the shared Progressly signing secret, server time, and employee access in TherapyPMS.')
    if status in (400, 422):
        return prefix + 'The assigned-forms request was rejected. Ask your administrator to check the employee mapping and API request details.'
    if status in (404, 405) or 300 <= status < 400:
        return prefix + 'The assigned-forms API address or route needs to be checked by your administrator.'
    if status == 409:
        return prefix + 'TherapyPMS rejected the single-use request. Refresh forms to send a new token.'
    if status == 429:
        return prefix + 'Too many requests. Please wait before refreshing forms.'
    if status >= 500:
        return prefix + 'The assigned-forms service is unavailable. Please try again later.'
    return prefix + 'The assigned forms could not be loaded. Please contact your administrator.'


def _parse_notes(payload) -> list[dict]:
    if (not isinstance(payload, dict) or payload.get('status') != 'success'
            or payload.get('source_type') != 'old_form_builder'
            or not isinstance(payload.get('notes'), list)):
        raise AssignedNotesError('Old Form Builder returned an invalid assigned forms list.')
    notes = []
    for note in payload['notes']:
        if (not isinstance(note, dict) or type(note.get('id')) is not int or note['id'] <= 0
                or not isinstance(note.get('name'), str) or not note['name'].strip()
                or not isinstance(note.get('url'), str)):
            raise AssignedNotesError('Old Form Builder returned an invalid assigned form.')
        url = note['url'].strip()
        try:
            URLValidator(schemes=['https'])(url)
            parsed = urlsplit(url)
            if parsed.username is not None or parsed.password is not None:
                raise ValueError
        except (ValidationError, ValueError):
            raise AssignedNotesError('Old Form Builder returned an invalid form link.') from None
        # Never forward the upstream envelope or arbitrary extra fields.
        notes.append({'id': note['id'], 'name': note['name'].strip(), 'url': url})
    return notes
