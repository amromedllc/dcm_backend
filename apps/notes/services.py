from django.utils import timezone
from ninja.errors import HttpError

from shared.audit import log_note_status_change
from .models import LessonNote, NoteTemplate


def resolve_template_tokens(note: LessonNote) -> dict[str, str]:
    """Resolve the ``[data-dynamic-field]`` tokens a 'forms' template embeds in
    its ``body_template`` into concrete strings for one note.

    Keys mirror the frontend's ``DYNAMIC_FIELDS_GROUPS`` (web templates page).
    A token that can't be resolved (no linked session/appointment, missing
    client, etc.) is simply omitted so the renderer falls back to showing the
    ``[Label]`` placeholder.
    """
    from apps.clients.models import Client
    from apps.sessions.models import Appointment

    if not (
        note.template_id
        and note.template
        and note.template.template_type == 'forms'
        and note.template.body_template
    ):
        return {}

    out: dict[str, str] = {}

    def put(key: str, value) -> None:
        if value not in (None, ''):
            out[key] = str(value)

    def role_label(user) -> str:
        getter = getattr(user, 'get_role_display', None)
        return getter() if callable(getter) else (getattr(user, 'role', '') or '')

    def fmt_time(dt) -> str:
        return timezone.localtime(dt).strftime('%-I:%M %p') if dt else ''

    def fmt_date(dt) -> str:
        return timezone.localtime(dt).date().isoformat() if dt else ''

    # ── Client ──────────────────────────────────────────────────────────────
    client = None
    if note.external_client_id is not None:
        client = (
            Client.objects.filter(id=note.external_client_id).first()
            or Client.objects.filter(external_id=str(note.external_client_id)).first()
        )
    if client:
        put('client.first_name', client.first_name)
        put('client.last_name', client.last_name)
        put('client.full_name', client.full_name)
        put('client.dob', client.date_of_birth.isoformat() if client.date_of_birth else '')
        put('client.id', client.id)

    # ── Authoring staff / user ──────────────────────────────────────────────
    staff = note.staff
    if staff:
        for prefix in ('user', 'staff'):
            put(f'{prefix}.full_name', staff.full_name)
            put(f'{prefix}.email', staff.email)
            put(f'{prefix}.role', role_label(staff))
        put('user.first_name', staff.first_name)
        put('user.last_name', staff.last_name)

    # ── Session ─────────────────────────────────────────────────────────────
    session = note.session_run
    if session:
        put('session.date', fmt_date(session.started_at) or note.note_date.isoformat())
        put('session.start_time', fmt_time(session.started_at))
        put('session.end_time', fmt_time(session.ended_at))
    else:
        put('session.date', note.note_date.isoformat())

    # ── Appointment ─────────────────────────────────────────────────────────
    appt_id = session.external_appointment_id if session else None
    if appt_id is not None:
        appt = (
            Appointment.objects.filter(id=appt_id).first()
            or Appointment.objects.filter(external_id=str(appt_id)).first()
        )
        if appt:
            put('appointment.id', appt.external_id or appt.id)
            put('appointment.date', fmt_date(appt.start_time))
            put('appointment.time', fmt_time(appt.start_time))

    return out


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
