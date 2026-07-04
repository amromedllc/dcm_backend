"""
SSO handoff into DocuSeal (docuseal/), which already exposes a
`GET /sso/login?token=<jwt>` endpoint built for a TherapyPMS-driven login.
This mints a token that endpoint will accept for a DCM user, scoping them to
a DocuSeal Account per facility/organization so PDF e-sign templates stay
correctly siloed per practice.
"""
import logging
from datetime import timedelta

import jwt
import requests
from django.conf import settings
from django.utils import timezone

from apps.accounts.models import User

logger = logging.getLogger(__name__)

SSO_TOKEN_TTL_MINUTES = 5
SESSION_NOTE_TREATMENT_TYPE = 'Session Note'


def _facility_for_user(user: User) -> tuple[str, str | None]:
    if user.external_admin_id is not None:
        facility_id = str(user.external_admin_id)
        facility_name = None
        try:
            from apps.legacy.models import TpmsAdmin
            admin = TpmsAdmin.objects.using('therapypms').filter(id=user.external_admin_id).first()
            if admin:
                facility_name = admin.name or admin.first_name
        except Exception:
            pass
        return facility_id, facility_name

    if user.organization_id is not None:
        return f'dcm-org-{user.organization_id}', user.organization.name

    return f'dcm-user-{user.id}', None


def _sso_token(user: User, **extra_claims) -> str:
    facility_id, facility_name = _facility_for_user(user)
    now = timezone.now()
    payload = {
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'user_type': user.role,
        'facility_id': facility_id,
        'facility_name': facility_name,
        'iat': now.timestamp(),
        'exp': (now + timedelta(minutes=SSO_TOKEN_TTL_MINUTES)).timestamp(),
        **extra_claims,
    }
    return jwt.encode(payload, settings.DOCUSEAL_SSO_SECRET, algorithm='HS256')


def build_sso_redirect_url(user: User) -> str:
    token = _sso_token(user)
    return f'{settings.DOCUSEAL_BASE_URL}/sso/login?token={token}'


def build_template_url(user: User, template_id: int | str) -> str:
    """
    Deep-links straight into a specific template's preview page
    (/templates/{id}/preview) instead of the dashboard root —
    sso_login_controller already redirects there when `template_id` is present
    in the JWT.
    """
    token = _sso_token(user, template_id=template_id)
    return f'{settings.DOCUSEAL_BASE_URL}/sso/login?token={token}'


def build_upload_url(user: User) -> str:
    """
    Deep-links straight into DocuSeal's "new template" upload dialog instead
    of the dashboard root. DCM's upload button only exists to create Session
    Note templates, so nothing here needs a folder tag or other marker —
    everything uploaded through this link is, by construction, a Session Note
    template (see list_uploaded_session_note_templates below).
    """
    token = _sso_token(user, redirect_to='new_template')
    return f'{settings.DOCUSEAL_BASE_URL}/sso/login?token={token}'


def list_session_note_templates(user: User) -> list[dict]:
    """
    Session Note DocuSeal templates for this user's facility, read directly
    from TPMS's `docu_seal_template_names` table.

    therapypms-api owns the DocuSeal sync (SuperAdminSettingController
    ::forms_builders_table) and the Session Note / Patient Intake / Staff
    Intake classification — DCM only reads the result, scoped to the caller's
    facility (external_admin_id). Users not linked to a TPMS facility have no
    DocuSeal templates to show.
    """
    if user.external_admin_id is None:
        return []

    from apps.legacy.models import TpmsDocusealTemplateName

    rows = TpmsDocusealTemplateName.objects.using('therapypms').filter(
        admin_id=user.external_admin_id,
        docu_seal_template_type=SESSION_NOTE_TREATMENT_TYPE,
    )
    return [
        {
            'id': row.pk,
            'external_template_id': str(row.template_id),
            'name': row.template_name or '',
            'treatment_type': row.docu_seal_template_type or '',
        }
        for row in rows
    ]


def _fetch_docuseal_access_token(user: User) -> str | None:
    """
    Per-facility DocuSeal API token — same email-lookup handoff
    therapypms-api's fetch_docu_seal_api_key() uses. DocuSeal maps the email
    to a token scoped to that user's Account, which SSO login already keeps
    in sync with the caller's facility (Account.tpms_admin_id).
    """
    try:
        response = requests.get(
            f'{settings.DOCUSEAL_BASE_URL}/public_access_token',
            params={'email': user.email},
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get('token')
    except requests.RequestException:
        logger.exception('Failed to fetch DocuSeal access token for %s', user.email)
        return None


def list_uploaded_session_note_templates(user: User) -> list[dict]:
    """
    Templates uploaded directly through DCM's upload flow (see
    build_upload_url), read back from this DocuSeal instance's own API for
    the caller's facility Account — a different source than
    list_session_note_templates() above, which reads TPMS's table synced from
    the separate forms.therapypms.com account. Every template in this
    account came from DCM's Session-Note-only upload button, so all of them
    are returned — no folder/tag filtering needed.
    """
    token = _fetch_docuseal_access_token(user)
    if not token:
        return []

    try:
        response = requests.get(
            f'{settings.DOCUSEAL_BASE_URL}/api/templates',
            headers={'X-Auth-Token': token},
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.exception('Failed to fetch DocuSeal templates for %s', user.email)
        return []

    return [
        {
            'id': template['id'],
            'name': template.get('name', ''),
            'folder_name': template.get('folder_name', ''),
            'created_at': template.get('created_at', ''),
        }
        for template in response.json().get('data', [])
    ]


def create_session_note_submission(user: User, template_id: int, external_id: str) -> dict:
    """
    Creates a DocuSeal submission from an uploaded template so the caller can
    fill/sign it immediately (send_email=False — no email invite, this is
    filled in-session, not mailed out).

    `external_id` is set to the DCM SessionRun id so the form.completed
    webhook (see verify_webhook_signature + the /docuseal/webhook receiver)
    can match the submission back to the right session without us having to
    track DocuSeal's submission id ourselves ahead of time.

    Returns the first submitter's {id, slug, embed_src} — with one submitter
    (the staff member filling it out) per submission, there's only one.
    """
    token = _fetch_docuseal_access_token(user)
    if not token:
        raise RuntimeError('No DocuSeal access token available for this user')

    response = requests.post(
        f'{settings.DOCUSEAL_BASE_URL}/api/templates/{template_id}/submissions',
        headers={'X-Auth-Token': token},
        json={
            'send_email': False,
            'submitters': [
                {
                    'name': f'{user.first_name} {user.last_name}'.strip() or user.email,
                    'email': user.email,
                    'external_id': external_id,
                },
            ],
        },
        timeout=10,
    )
    response.raise_for_status()
    submitters = response.json()
    submitter = submitters[0]
    return {
        'id': submitter['id'],
        'slug': submitter['slug'],
        'url': submitter['embed_src'],
    }


def verify_webhook_signature(request) -> bool:
    secret = request.headers.get('X-Dcm-Webhook-Secret')
    return bool(settings.DOCUSEAL_WEBHOOK_SECRET) and secret == settings.DOCUSEAL_WEBHOOK_SECRET


def build_submission_url(slug: str) -> str:
    """/s/{slug} is a public DocuSeal URL (no session/auth needed) — safe to
    reconstruct directly from a stored slug rather than re-minting anything."""
    return f'{settings.DOCUSEAL_BASE_URL}/s/{slug}'
