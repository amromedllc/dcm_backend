import json

from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
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


class SsoUrlResponse(Schema):
    url: str


@router.get('/docuseal/sso-url', response=SsoUrlResponse)
def docuseal_sso_url(request):
    return {'url': build_sso_redirect_url(request.user)}


@router.get('/docuseal/upload-url', response=SsoUrlResponse)
def docuseal_upload_url(request):
    """Deep link straight into DocuSeal's upload dialog."""
    return {'url': build_upload_url(request.user)}


class DocusealTemplateSchema(Schema):
    id: int
    external_template_id: str
    name: str
    treatment_type: str


@router.get('/docuseal/templates', response=list[DocusealTemplateSchema])
def list_docuseal_session_note_templates(request):
    """Session Note DocuSeal templates for the caller's facility, read from TPMS."""
    return list_session_note_templates(request.user)


class DocusealUploadedTemplateSchema(Schema):
    id: int
    name: str
    folder_name: str
    created_at: str


@router.get('/docuseal/uploaded-templates', response=list[DocusealUploadedTemplateSchema])
def list_docuseal_uploaded_templates(request):
    """Session Note templates uploaded directly through DCM's upload flow."""
    return list_uploaded_session_note_templates(request.user)


@router.get('/docuseal/uploaded-templates/{template_id}/url', response=SsoUrlResponse)
def docuseal_uploaded_template_url(request, template_id: int):
    """Deep link straight into this specific uploaded template's preview page."""
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
    from apps.notes.models import LessonNote
    note = LessonNote.objects.filter(session_run_id=session_run_id).first()
    return _serialize_note_status(session_run_id, note)


@router.post('/docuseal/session-notes/{session_run_id}', response=SessionNoteStatusSchema)
def start_session_note(request, session_run_id: int, data: SessionNoteStartRequest):
    """Creates (or re-fetches) the DocuSeal submission for this session's note."""
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

    updated = LessonNote.objects.filter(docuseal_submitter_id=submitter_id).update(
        docuseal_completed_at=timezone.now(),
    )
    return {'status': 'ok', 'updated': updated}
