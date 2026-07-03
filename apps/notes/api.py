import hashlib
from datetime import date
from django.db.models import Q
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from .models import LessonNote, NoteTemplate, NoteSignature, NoteAssignment
from .schemas import (
    LessonNoteSchema, LessonNoteListSchema, NoteCreateRequest, NoteUpdateRequest,
    NoteRejectRequest, NoteSignatureSchema, SignNoteRequest,
    NoteTemplateSchema, NoteTemplateCreateRequest, NoteTemplateUpdateRequest,
    ReviewQueueItem,
    NoteAssignmentSchema, NoteAssignmentCreateRequest,
)
from .services import submit_note, approve_note, reject_note

router = Router(auth=jwt_auth)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_supervisor(request):
    if request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')


def _get_note_or_404(note_id: int) -> LessonNote:
    try:
        return LessonNote.objects.select_related('template').prefetch_related('signatures').get(id=note_id)
    except LessonNote.DoesNotExist:
        raise HttpError(404, 'Note not found')


def _assert_note_access(note: LessonNote, request) -> None:
    """Staff can only access their own notes; supervisors/admins can access all."""
    if request.user.role == 'staff' and note.staff_id != request.user.id:
        raise HttpError(403, 'Access denied')


def _serialize_note(note: LessonNote) -> dict:
    return {
        'id': note.id,
        'client_id': note.external_client_id,
        'session_run_id': note.session_run_id,
        'staff_id': note.staff_id,
        'template_id': note.template_id,
        'note_date': note.note_date,
        'body': note.body,
        'status': note.status,
        'submitted_at': note.submitted_at,
        'approved_by_id': note.approved_by_id,
        'approved_at': note.approved_at,
        'rejected_by_id': note.rejected_by_id,
        'rejected_at': note.rejected_at,
        'rejection_reason': note.rejection_reason,
        'requires_caregiver_signature': note.requires_caregiver_signature,
        'signatures': [
            {
                'id': sig.id,
                'note_id': sig.note_id,
                'signer_id': sig.signer_id,
                'signer_name': sig.signer_name,
                'signer_role': sig.signer_role,
                'signature_type': sig.signature_type,
                'signed_at': sig.signed_at,
            }
            for sig in note.signatures.all()
        ],
        'created_at': note.created_at,
        'updated_at': note.updated_at,
    }


# ---------------------------------------------------------------------------
# Note templates
# ---------------------------------------------------------------------------

@router.get('/templates/notes', response=list[NoteTemplateSchema])
def list_note_templates(request):
    return list(NoteTemplate.objects.filter(is_active=True))


@router.post('/templates/notes', response={201: NoteTemplateSchema})
def create_note_template(request, data: NoteTemplateCreateRequest):
    _require_supervisor(request)
    template = NoteTemplate.objects.create(created_by=request.user, **data.dict())
    return 201, template


@router.get('/templates/notes/{template_id}', response=NoteTemplateSchema)
def get_note_template(request, template_id: int):
    try:
        return NoteTemplate.objects.get(id=template_id)
    except NoteTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')


@router.patch('/templates/notes/{template_id}', response=NoteTemplateSchema)
def update_note_template(request, template_id: int, data: NoteTemplateUpdateRequest):
    _require_supervisor(request)
    try:
        template = NoteTemplate.objects.get(id=template_id)
    except NoteTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(template, field, value)
    template.save()
    return template


@router.delete('/templates/notes/{template_id}', response={204: None})
def delete_note_template(request, template_id: int):
    _require_supervisor(request)
    try:
        NoteTemplate.objects.get(id=template_id).delete()
    except NoteTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')
    return 204, None


# ---------------------------------------------------------------------------
# Notes — CRUD
# ---------------------------------------------------------------------------

@router.get('/notes', response=list[LessonNoteListSchema])
def list_notes(
    request,
    client_id: int | None = None,
    status: str | None = None,
    staff_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    qs = LessonNote.objects.all()

    # Scope to the requesting user's accessible records
    if request.user.role == 'staff':
        qs = qs.filter(staff_id=request.user.id)
    elif staff_id:
        qs = qs.filter(staff_id=staff_id)

    if client_id:
        qs = qs.filter(external_client_id=client_id)
    if status:
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(note_date__gte=date_from)
    if date_to:
        qs = qs.filter(note_date__lte=date_to)

    result = []
    for note in qs.select_related('staff', 'template').prefetch_related('signatures'):
        staff = note.staff
        staff_name = f'{staff.first_name} {staff.last_name}'.strip() if staff else None
        result.append({
            'id': note.id,
            'client_id': note.client_id,
            'session_run_id': note.session_run_id,
            'staff_id': note.staff_id,
            'staff_name': staff_name or (staff.email if staff else None),
            'template_id': note.template_id,
            'template_name': note.template.name if note.template else None,
            'note_date': note.note_date,
            'status': note.status,
            'submitted_at': note.submitted_at,
            'requires_caregiver_signature': note.requires_caregiver_signature,
            'signature_count': note.signatures.count(),
            'created_at': note.created_at,
            'updated_at': note.updated_at,
        })
    return result


@router.post('/notes', response={201: LessonNoteSchema})
def create_note(request, data: NoteCreateRequest):
    payload = data.dict()
    external_client_id = payload.pop('client_id', None)
    assignment_id = payload.pop('assignment_id', None)
    note = LessonNote.objects.create(
        staff=request.user,
        created_by=request.user,
        external_client_id=external_client_id,
        **payload,
    )
    if assignment_id:
        NoteAssignment.objects.filter(id=assignment_id).update(note=note)
    note = LessonNote.objects.prefetch_related('signatures').get(id=note.id)
    return 201, _serialize_note(note)


@router.get('/notes/{note_id}', response=LessonNoteSchema)
def get_note(request, note_id: int):
    note = _get_note_or_404(note_id)
    _assert_note_access(note, request)
    return _serialize_note(note)


@router.patch('/notes/{note_id}', response=LessonNoteSchema)
def update_note(request, note_id: int, data: NoteUpdateRequest):
    note = _get_note_or_404(note_id)
    _assert_note_access(note, request)
    if not note.is_editable:
        raise HttpError(409, f'Note is {note.status} and cannot be edited')
    for field, value in data.dict(exclude_none=True).items():
        setattr(note, field, value)
    note.save()
    note.refresh_from_db()
    return _serialize_note(note)


@router.delete('/notes/{note_id}', response={204: None})
def delete_note(request, note_id: int):
    note = _get_note_or_404(note_id)
    _assert_note_access(note, request)
    if note.status not in (LessonNote.Status.DRAFT, LessonNote.Status.REJECTED):
        raise HttpError(409, 'Only draft or rejected notes can be deleted')
    note.delete()
    return 204, None


# ---------------------------------------------------------------------------
# Note workflow — submit / approve / reject
# ---------------------------------------------------------------------------

@router.post('/notes/{note_id}/submit', response=LessonNoteSchema)
def submit(request, note_id: int):
    note = _get_note_or_404(note_id)
    _assert_note_access(note, request)
    submit_note(note, request.user)
    note.refresh_from_db()
    return _serialize_note(note)


@router.post('/notes/{note_id}/approve', response=LessonNoteSchema)
def approve(request, note_id: int):
    _require_supervisor(request)
    note = _get_note_or_404(note_id)
    approve_note(note, request.user)
    note.refresh_from_db()
    return _serialize_note(note)


@router.post('/notes/{note_id}/reject', response=LessonNoteSchema)
def reject(request, note_id: int, data: NoteRejectRequest):
    _require_supervisor(request)
    note = _get_note_or_404(note_id)
    reject_note(note, request.user, data.reason)
    note.refresh_from_db()
    return _serialize_note(note)


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------

@router.get('/notes/{note_id}/signatures', response=list[NoteSignatureSchema])
def list_signatures(request, note_id: int):
    note = _get_note_or_404(note_id)
    _assert_note_access(note, request)
    return list(note.signatures.all())


@router.post('/notes/{note_id}/sign', response={201: NoteSignatureSchema})
def sign_note(request, note_id: int, data: SignNoteRequest):
    note = _get_note_or_404(note_id)

    # Approved notes can be signed by staff/supervisors; caregiver signing allowed on any non-draft
    if data.signature_type == 'caregiver':
        if note.status == LessonNote.Status.DRAFT:
            raise HttpError(409, 'Caregivers cannot sign draft notes')
        if not note.requires_caregiver_signature:
            raise HttpError(400, 'This note does not require a caregiver signature')
    else:
        if note.status != LessonNote.Status.APPROVED:
            raise HttpError(409, 'Staff and supervisor signatures require an approved note')

    # Prevent duplicate signature by the same person and type
    if NoteSignature.objects.filter(note_id=note_id, signer_id=request.user.id, signature_type=data.signature_type).exists():
        raise HttpError(409, 'You have already signed this note with this signature type')

    # Hash IP for privacy — request.META may not always have it in proxied setups
    raw_ip = request.META.get('REMOTE_ADDR', '')
    ip_hash = hashlib.sha256(raw_ip.encode()).hexdigest() if raw_ip else ''

    sig = NoteSignature.objects.create(
        note=note,
        signer_id=request.user.id,
        signer_name=request.user.full_name,
        signer_role=request.user.role,
        signature_type=data.signature_type,
        signature_data=data.signature_data,
        ip_address_hash=ip_hash,
    )
    return 201, sig


# ---------------------------------------------------------------------------
# Supervisor review queue
# ---------------------------------------------------------------------------

@router.get('/notes/review-queue', response=list[ReviewQueueItem])
def review_queue(
    request,
    client_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    """Returns all submitted notes awaiting supervisory review, oldest first."""
    _require_supervisor(request)
    qs = (
        LessonNote.objects
        .filter(status=LessonNote.Status.SUBMITTED)
        .select_related('template')
        .order_by('submitted_at')
    )
    if client_id:
        qs = qs.filter(external_client_id=client_id)
    if date_from:
        qs = qs.filter(note_date__gte=date_from)
    if date_to:
        qs = qs.filter(note_date__lte=date_to)

    return [
        {
            'id': note.id,
            'client_id': note.client_id,
            'staff_id': note.staff_id,
            'note_date': note.note_date,
            'submitted_at': note.submitted_at,
            'template_name': note.template.name if note.template else None,
            'session_run_id': note.session_run_id,
        }
        for note in qs
    ]


# ---------------------------------------------------------------------------
# Note assignments — admin/supervisor assigns templates to appointments
# ---------------------------------------------------------------------------

def _serialize_assignment(a: NoteAssignment) -> dict:
    assigned_by = a.assigned_by
    return {
        'id': a.id,
        'external_appointment_id': a.external_appointment_id,
        'external_client_id': a.external_client_id,
        'template_id': a.template_id,
        'template_name': a.template.name,
        'is_filled': a.is_filled,
        'note_id': a.note_id,
        'assigned_by_name': (
            f'{assigned_by.first_name} {assigned_by.last_name}'.strip() or assigned_by.email
            if assigned_by else None
        ),
        'created_at': a.created_at,
    }


@router.get('/notes/assignments', response=list[NoteAssignmentSchema])
def list_assignments(request, appointment_id: int):
    qs = (
        NoteAssignment.objects
        .filter(external_appointment_id=appointment_id)
        .select_related('template', 'assigned_by')
    )
    return [_serialize_assignment(a) for a in qs]


@router.post('/notes/assignments', response={201: NoteAssignmentSchema})
def create_assignment(request, data: NoteAssignmentCreateRequest):
    _require_supervisor(request)
    try:
        template = NoteTemplate.objects.get(id=data.template_id)
    except NoteTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')

    assignment, created = NoteAssignment.objects.get_or_create(
        external_appointment_id=data.external_appointment_id,
        template=template,
        defaults={
            'external_client_id': data.external_client_id,
            'assigned_by': request.user,
            'created_by': request.user,
        },
    )
    if not created:
        raise HttpError(409, 'This template is already assigned to this appointment')
    assignment = NoteAssignment.objects.select_related('template', 'assigned_by').get(id=assignment.id)
    return 201, _serialize_assignment(assignment)


@router.delete('/notes/assignments/{assignment_id}', response={204: None})
def delete_assignment(request, assignment_id: int):
    _require_supervisor(request)
    try:
        a = NoteAssignment.objects.get(id=assignment_id)
    except NoteAssignment.DoesNotExist:
        raise HttpError(404, 'Assignment not found')
    if a.is_filled:
        raise HttpError(409, 'Cannot remove an assignment that has already been filled')
    a.delete()
    return 204, None

