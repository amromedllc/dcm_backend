"""
Telehealth (zoom-backend) client — server-to-server bridge into the
LiveKit-based video system at ZOOM_BACKEND_URL (repos: zoom-backend,
zoom-frontend — a self-hosted "Zoom-like" product, unrelated to zoom.us).

Flow this module implements (see zoom-backend/app/api/v1/sso.py + livekit.py):
1. DCM signs a short-lived JWT with TELEHEALTH_SSO_SECRET_KEY (must match
   zoom-backend's own SSO_SECRET_KEY) carrying {meet_url, user_type, email,
   "Display name", admin_id} — the exact SSOPayload shape zoom-backend
   expects.
2. POST /api/v1/sso/login with that JWT → zoom-backend resolves meet_url to
   a room_name (via its own meet_links table) and returns its own internal
   {access_token, room_name}.
3. GET /api/v1/livekit/connection-details (Bearer: that access_token) →
   {server_url, participant_token} — what the browser's LiveKit client SDK
   needs to actually join the room and render video in-page.

TPMS's `telehealth_link` (see clients.api._appointment_telehealth_link) is a
URL into zoom-frontend's /[slug] page — meet_url() below pulls the slug
(zoom-backend's "meet_url") off the end of that link.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from urllib.parse import urlparse

import jwt as pyjwt
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15
SSO_TOKEN_TTL_SECONDS = 120


class TelehealthError(Exception):
    """Raised when zoom-backend rejects the SSO handshake or token request."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class TelehealthConnectionDetails:
    server_url: str
    participant_token: str
    room_name: str
    participant_name: str


def meet_url_from_link(telehealth_link: str) -> str:
    """Pull the meet_url slug off a zoom-frontend join link, e.g.
    "https://meet.example.com/abc123" -> "abc123". Falls back to the raw
    input if it isn't a URL (already a bare slug)."""
    link = (telehealth_link or '').strip()
    if not link:
        raise TelehealthError('Missing telehealth link')

    path = urlparse(link).path if '://' in link else link
    slug = path.rstrip('/').rsplit('/', 1)[-1]
    if not slug:
        raise TelehealthError(f'Could not extract a meet_url slug from "{telehealth_link}"')
    return slug


def _base_url() -> str:
    return getattr(settings, 'ZOOM_BACKEND_URL', '').rstrip('/')


def _sign_sso_token(*, meet_url: str, email: str, display_name: str, admin_id: str, user_type: str) -> str:
    secret = getattr(settings, 'TELEHEALTH_SSO_SECRET_KEY', '')
    if not secret:
        raise TelehealthError('Telehealth is not configured (SSO_SECRET_KEY missing)')

    now = datetime.now(dt_timezone.utc)
    payload = {
        'admin_id': admin_id,
        'Display name': display_name,
        'email': email,
        'meet_url': meet_url,
        'user_type': user_type,
        'iat': now,
        'exp': now + timedelta(seconds=SSO_TOKEN_TTL_SECONDS),
    }
    return pyjwt.encode(payload, secret, algorithm='HS256')


def _sso_login(sso_token: str) -> dict[str, Any]:
    url = f'{_base_url()}/api/v1/sso/login'
    try:
        response = requests.post(
            url,
            json={'token': sso_token},
            headers={'Accept': 'application/json'},
            timeout=getattr(settings, 'TELEHEALTH_API_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS),
        )
    except requests.RequestException as exc:
        logger.warning('Telehealth SSO login request failed: %s', exc)
        raise TelehealthError('Telehealth service unavailable') from exc

    if response.status_code >= 400:
        logger.warning('Telehealth SSO login failed: status=%s body=%s', response.status_code, response.text[:1000])
        raise TelehealthError('Telehealth session could not be started', status_code=response.status_code)

    try:
        return response.json()
    except ValueError as exc:
        raise TelehealthError('Telehealth service returned an invalid response') from exc


def _connection_details(*, access_token: str, room_name: str, participant_name: str) -> dict[str, Any]:
    url = f'{_base_url()}/api/v1/livekit/connection-details'
    try:
        response = requests.get(
            url,
            params={'room_name': room_name, 'participant_name': participant_name},
            headers={'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'},
            timeout=getattr(settings, 'TELEHEALTH_API_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS),
        )
    except requests.RequestException as exc:
        logger.warning('Telehealth connection-details request failed: %s', exc)
        raise TelehealthError('Telehealth service unavailable') from exc

    if response.status_code >= 400:
        logger.warning(
            'Telehealth connection-details failed: status=%s body=%s',
            response.status_code, response.text[:1000],
        )
        raise TelehealthError('Could not get a video connection for this session', status_code=response.status_code)

    try:
        return response.json()
    except ValueError as exc:
        raise TelehealthError('Telehealth service returned an invalid response') from exc


def _mangle_room_name_to_match_zoom_frontend(room_name: str) -> str:
    """
    zoom-frontend's own client-side navigation (JoinForm.tsx, sso/page.tsx —
    both router.push()/router.replace() to `/rooms/${roomName}?...` with the
    room name inserted unencoded) ends up sending the browser to a room
    whose *actual* LiveKit room name is the raw string with
    encodeURIComponent applied — e.g. "Antony  Ashwinth's Room" becomes
    "Antony%20%20Ashwinth's%20Room" as literal characters, not decoded back.
    Confirmed directly against the LiveKit server's own room list
    (2026-09-01): two real, separate rooms existed side by side, one clean
    and one with literal "%20%20" in its name, and every browser session
    going through zoom-frontend's own pages consistently lands in the
    mangled one (deterministic, not random — that's why two zoom-frontend
    users always find each other, but DCM's own clean resolution never
    matched them).

    zoom-frontend is out of scope to fix here, so DCM instead reproduces
    the exact same transform so its LiveKit connection lands in the same
    room real web participants are actually in. Matches JS's
    encodeURIComponent's safe set (unlike urllib.parse.quote's default,
    which additionally escapes !~*'() ).

    NOTE: if zoom-frontend's own encoding bug is ever fixed upstream, this
    must be removed at the same time — otherwise DCM would be the one
    landing in the wrong room instead.
    """
    from urllib.parse import quote
    return quote(room_name, safe="!~*'()")


def _sso_login_for_link(
    *,
    telehealth_link: str,
    email: str,
    display_name: str,
    admin_id: int | str | None,
    user_type: str,
) -> tuple[str, str]:
    """Sign + run the SSO handshake for a telehealth_link, returning
    (access_token, room_name). Tokens aren't cached/reused across calls —
    each caller (connect, admit, ...) re-runs this fresh, since a video call
    can outlast the short-lived SSO/internal tokens by a wide margin."""
    slug = meet_url_from_link(telehealth_link)

    sso_token = _sign_sso_token(
        meet_url=slug,
        email=email,
        display_name=display_name or email,
        admin_id=str(admin_id) if admin_id is not None else '0',
        user_type=user_type,
    )
    sso_result = _sso_login(sso_token)

    access_token = sso_result.get('access_token')
    room_name = sso_result.get('room_name')
    if not access_token or not room_name:
        raise TelehealthError('Telehealth SSO response was missing access_token/room_name')

    return access_token, _mangle_room_name_to_match_zoom_frontend(room_name)


def get_connection_details(
    *,
    telehealth_link: str,
    email: str,
    display_name: str,
    admin_id: int | str | None,
    user_type: str,
) -> TelehealthConnectionDetails:
    access_token, room_name = _sso_login_for_link(
        telehealth_link=telehealth_link,
        email=email,
        display_name=display_name,
        admin_id=admin_id,
        user_type=user_type,
    )

    details = _connection_details(
        access_token=access_token,
        room_name=room_name,
        participant_name=display_name or email,
    )

    server_url = details.get('server_url')
    participant_token = details.get('participant_token')
    if not server_url or not participant_token:
        raise TelehealthError('Telehealth service did not return a usable video connection')

    return TelehealthConnectionDetails(
        server_url=server_url,
        participant_token=participant_token,
        room_name=room_name,
        participant_name=details.get('participant_name') or display_name or email,
    )


def admit_participant(
    *,
    telehealth_link: str,
    identity: str,
    email: str,
    display_name: str,
    admin_id: int | str | None,
    user_type: str,
) -> None:
    """
    Admit a waiting client-role participant — zoom-backend issues
    client-role tokens with can_publish/can_subscribe both false and
    metadata {"status": "waiting"} (see livekit_service.generate_token in
    zoom-backend) until an admin/employee explicitly admits them via
    POST /api/v1/livekit/admit. `identity` is that participant's LiveKit
    identity (JWT "sub" claim) — the frontend reads it off the
    RemoteParticipant object once it sees them with status "waiting".

    Only admin/employee-role callers can succeed here — zoom-backend itself
    enforces that (403 otherwise), this doesn't re-check it.
    """
    access_token, room_name = _sso_login_for_link(
        telehealth_link=telehealth_link,
        email=email,
        display_name=display_name,
        admin_id=admin_id,
        user_type=user_type,
    )

    url = f'{_base_url()}/api/v1/livekit/admit'
    try:
        response = requests.post(
            url,
            json={'room_name': room_name, 'identity': identity},
            headers={'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'},
            timeout=getattr(settings, 'TELEHEALTH_API_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS),
        )
    except requests.RequestException as exc:
        logger.warning('Telehealth admit request failed: %s', exc)
        raise TelehealthError('Telehealth service unavailable') from exc

    if response.status_code >= 400:
        logger.warning(
            'Telehealth admit failed: status=%s body=%s',
            response.status_code, response.text[:1000],
        )
        raise TelehealthError('Could not admit participant', status_code=response.status_code)
