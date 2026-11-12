import json

from django.http import HttpResponse
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from apps.accounts.permissions import require_permission
from .docuseal import (
    build_sso_redirect_url,
    build_submission_url,
    build_template_url,
    build_upload_url,
    create_session_note_submission,
    list_session_note_templates,
    list_uploaded_session_note_templates,
    verify_webhook_signature,
)

router = Router(auth=jwt_auth)


class FormBuilderSettingsSchema(Schema):
    mode: str | None
    new_enabled: bool
    old_enabled: bool


@router.get('/form-builder-settings', response=FormBuilderSettingsSchema)
def get_form_builder_settings(request):
    require_permission(request, 'templates_view')
    from .form_builders import form_builder_mode

    mode = form_builder_mode(request)
    return {'mode': mode, 'new_enabled': mode in ('new', 'both'), 'old_enabled': mode in ('old', 'both')}


class OldAssignedNoteSchema(Schema):
    id: int
    name: str
    url: str


@router.get('/old-form-builder/assigned-notes', response=list[OldAssignedNoteSchema])
def get_old_assigned_notes(request, response: HttpResponse, session_run_id: int | None = None):
    """session_run_id is optional and, when given, must be a DCM SessionRun id
    (never a raw TPMS appointment id taken from the client) — looked up and
    permission-checked server-side so the resulting TPMS session_id can't be
    forged to tie a note to a session the caller doesn't own."""
    require_permission(request, 'templates_view')
    from .form_builders import require_form_builder
    from .assigned_notes_client import AssignedNotesError, fetch_assigned_session_notes

    require_form_builder(request, 'old')
    response['Cache-Control'] = 'private, no-store'

    tpms_session_id = 0
    if session_run_id is not None:
        from apps.sessions.models import SessionRun
        try:
            session_run = SessionRun.objects.get(id=session_run_id)
        except SessionRun.DoesNotExist:
            raise HttpError(404, 'Session not found')
        if request.user.role == 'staff' and session_run.staff_id != request.user.id:
            raise HttpError(403, 'Access denied')
        tpms_session_id = session_run.external_appointment_id or 0

    try:
        return fetch_assigned_session_notes(request.user, tpms_session_id)
    except AssignedNotesError as exc:
        raise HttpError(exc.status, str(exc)) from None


class SsoUrlResponse(Schema):
    url: str


@router.get('/docuseal/sso-url', response=SsoUrlResponse)
def docuseal_sso_url(request):
    require_permission(request, 'templates_view')
    from .form_builders import require_form_builder
    require_form_builder(request, 'new')
    return {'url': build_sso_redirect_url(request.user)}


@router.get('/docuseal/upload-url', response=SsoUrlResponse)
def docuseal_upload_url(request):
    """Deep link straight into DocuSeal's upload dialog."""
    require_permission(request, 'templates_create')
    from .form_builders import require_form_builder
    require_form_builder(request, 'new')
    return {'url': build_upload_url(request.user)}


class DocusealTemplateSchema(Schema):
    id: int
    external_template_id: str
    name: str
    treatment_type: str


@router.get('/docuseal/templates', response=list[DocusealTemplateSchema])
def list_docuseal_session_note_templates(request):
    """Session Note DocuSeal templates for the caller's facility, read from TPMS."""
    require_permission(request, 'templates_view')
    from .form_builders import require_form_builder
    require_form_builder(request, 'new')
    return list_session_note_templates(request.user)


class DocusealUploadedTemplateSchema(Schema):
    id: int
    name: str
    folder_name: str
    created_at: str


@router.get('/docuseal/uploaded-templates', response=list[DocusealUploadedTemplateSchema])
def list_docuseal_uploaded_templates(request):
    """Session Note templates uploaded directly through DCM's upload flow."""
    require_permission(request, 'templates_view')
    from .form_builders import require_form_builder
    require_form_builder(request, 'new')
    return list_uploaded_session_note_templates(request.user)


@router.get('/docuseal/uploaded-templates/{template_id}/url', response=SsoUrlResponse)
def docuseal_uploaded_template_url(request, template_id: int):
    """Deep link straight into this specific uploaded template's preview page."""
    require_permission(request, 'templates_view')
    from .form_builders import require_form_builder
    require_form_builder(request, 'new')
    return {'url': build_template_url(request.user, template_id)}


# ---------------------------------------------------------------------------
# Session Notes filled via DocuSeal — see apps.sessions.services.submit_session
# for the gate this feeds (a session can't submit until its selected template
# is completed).
# ---------------------------------------------------------------------------

class SessionNoteStatusSchema(Schema):
    session_run_id: int
    template_id: int | None = None
    url: str | None = None
    completed: bool = False
    completed_at: str | None = None


class SessionNoteStartRequest(Schema):
    template_id: int


def _get_or_create_note(session_run_id: int):
    from apps.sessions.models import SessionRun
    from apps.notes.models import LessonNote

    try:
        session_run = SessionRun.objects.get(id=session_run_id)
    except SessionRun.DoesNotExist:
        raise HttpError(404, 'Session not found')

    note, _ = LessonNote.objects.get_or_create(
        session_run=session_run,
        defaults={
            'external_client_id': session_run.external_client_id,
            'staff': session_run.staff,
            'note_date': timezone.now().date(),
        },
    )
    return session_run, note


def _serialize_note_status(session_run_id: int, note) -> dict:
    return {
        'session_run_id': session_run_id,
        'template_id': note.docuseal_template_id if note else None,
        'url': build_submission_url(note.docuseal_slug) if note and note.docuseal_slug else None,
        'completed': bool(note and note.docuseal_completed_at),
        'completed_at': note.docuseal_completed_at.isoformat() if note and note.docuseal_completed_at else None,
    }


@router.get('/docuseal/session-notes/{session_run_id}', response=SessionNoteStatusSchema)
def get_session_note_status(request, session_run_id: int):
    from apps.accounts.permissions import resolve_permission_organization, user_has_permission
    from apps.notes.models import LessonNote
    organization = resolve_permission_organization(request)
    if not any(user_has_permission(request.user, organization, p) for p in ('client_notes', 'notes_view', 'notes_create')):
        raise HttpError(403, 'Insufficient permissions')
    note = LessonNote.objects.filter(session_run_id=session_run_id).first()
    return _serialize_note_status(session_run_id, note)


@router.post('/docuseal/session-notes/{session_run_id}', response=SessionNoteStatusSchema)
def start_session_note(request, session_run_id: int, data: SessionNoteStartRequest):
    """Creates (or re-fetches) the DocuSeal submission for this session's note."""
    require_permission(request, 'notes_create')
    session_run, note = _get_or_create_note(session_run_id)

    if note.docuseal_template_id == data.template_id and note.docuseal_slug:
        return _serialize_note_status(session_run_id, note)

    submitter = create_session_note_submission(
        request.user, data.template_id, external_id=str(session_run_id),
    )
    note.docuseal_template_id = data.template_id
    note.docuseal_submitter_id = submitter['id']
    note.docuseal_slug = submitter['slug']
    note.docuseal_completed_at = None
    note.save(update_fields=['docuseal_template_id', 'docuseal_submitter_id', 'docuseal_slug', 'docuseal_completed_at'])

    return _serialize_note_status(session_run_id, note)


@router.post('/docuseal/webhook', auth=None)
def docuseal_webhook(request):
    """Unauthenticated by JWT — verified via a shared secret header instead
    (see docuseal/app/models/account.rb, which provisions this on every
    Account's WebhookUrl)."""
    if not verify_webhook_signature(request):
        raise HttpError(401, 'Invalid webhook signature')

    payload = json.loads(request.body)
    if payload.get('event_type') != 'form.completed':
        return {'status': 'ignored'}

    from apps.notes.models import LessonNote

    data = payload.get('data', {})
    submitter_id = data.get('id')
    if submitter_id is None:
        return {'status': 'ignored'}

    # TenantResolverMiddleware can't resolve a real tenant for this request
    # (DocuSeal calls one shared webhook URL, not a per-org hostname, and
    # there's no JWT), so it falls back to an arbitrary org. Bypass row-level
    # scoping and match on the globally-unique docuseal_submitter_id instead.
    updated = LessonNote.all_organizations.filter(docuseal_submitter_id=submitter_id).update(
        docuseal_completed_at=timezone.now(),
    )
    return {'status': 'ok', 'updated': updated}
