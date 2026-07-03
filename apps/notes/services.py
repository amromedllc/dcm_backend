from django.utils import timezone
from ninja.errors import HttpError

from shared.audit import log_note_status_change
from .models import LessonNote, NoteTemplate


def _validate_required_fields(note: LessonNote) -> None:
    """
    Checks that all required template fields have non-empty values in note.body.
    Raises 422 with a descriptive message listing missing fields.
    """
    if not note.template_id:
        return

    template = note.template
    missing = [
        f['label']
        for f in template.fields
        if f.get('required') and not note.body.get(f['key'])
    ]
    if missing:
        raise HttpError(422, f'Required fields are missing: {", ".join(missing)}')


def _assert_editable(note: LessonNote) -> None:
    if not note.is_editable:
        raise HttpError(409, f'Note is {note.status} and cannot be modified')


def submit_note(note: LessonNote, staff_user) -> None:
    """Draft → Submitted. Validates required fields first."""
    if note.status not in (LessonNote.Status.DRAFT, LessonNote.Status.REJECTED):
        raise HttpError(409, f'Note must be a draft or rejected to submit (current: {note.status})')
    if note.staff_id != staff_user.id and staff_user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Only the note author or a supervisor can submit')
    _validate_required_fields(note)
    old_status = note.status
    note.status = LessonNote.Status.SUBMITTED
    note.submitted_at = timezone.now()
    note.rejection_reason = ''
    note.rejected_by_id = None
    note.rejected_at = None
    note.save(update_fields=['status', 'submitted_at', 'rejection_reason', 'rejected_by_id', 'rejected_at'])
    log_note_status_change(staff_user.id, note.id, old_status, note.status)


def approve_note(note: LessonNote, reviewer) -> None:
    """Submitted → Approved."""
    if note.status != LessonNote.Status.SUBMITTED:
        raise HttpError(409, f'Note must be submitted before approval (current: {note.status})')
    old_status = note.status
    note.status = LessonNote.Status.APPROVED
    note.approved_by_id = reviewer.id
    note.approved_at = timezone.now()
    note.save(update_fields=['status', 'approved_by_id', 'approved_at'])
    log_note_status_change(reviewer.id, note.id, old_status, note.status)


def reject_note(note: LessonNote, reviewer, reason: str) -> None:
    """Submitted → Rejected. A reason is mandatory."""
    if note.status != LessonNote.Status.SUBMITTED:
        raise HttpError(409, f'Note must be submitted before rejection (current: {note.status})')
    if not reason.strip():
        raise HttpError(400, 'A rejection reason is required')
    old_status = note.status
    note.status = LessonNote.Status.REJECTED
    note.rejected_by_id = reviewer.id
    note.rejected_at = timezone.now()
    note.rejection_reason = reason
    note.save(update_fields=['status', 'rejected_by_id', 'rejected_at', 'rejection_reason'])
    log_note_status_change(reviewer.id, note.id, old_status, note.status)
