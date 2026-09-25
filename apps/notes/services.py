from django.utils import timezone
from ninja.errors import HttpError

from shared.audit import log_note_status_change
from .models import LessonNote, NoteTemplate


def _label(value: str) -> str:
    return (value or '').replace('_', ' ').strip().title()


def _hms(total_seconds: int) -> str:
    return f'{total_seconds // 3600}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}'


def resolve_program_tokens(session) -> dict[str, str]:
    """Values for the ``program.*`` tokens, built from what was recorded in one session.

    Multi-line values use newlines (one entry per line). Anything with no data is omitted so the
    note falls back to the ``[Label]`` placeholder."""
    from apps.analytics.services import _max_scores_for_targets
    from apps.programs.models import Target, TargetPromptLevelChange, TargetStatusChange
    from apps.sessions.models import BehaviorEvent, TrialEvent

    out: dict[str, str] = {}

    trials = list(TrialEvent.objects.filter(session_run=session).values('target_id', 'response_score'))
    behaviors = list(
        BehaviorEvent.objects.filter(session_run=session).values('target_id', 'duration_seconds', 'frequency_count')
    )
    target_ids = sorted({row['target_id'] for row in trials} | {row['target_id'] for row in behaviors})
    targets = {
        t.id: t for t in Target.objects.filter(id__in=target_ids).select_related('program').order_by('program__name', 'display_order', 'id')
    }
    max_scores = _max_scores_for_targets(target_ids) if target_ids else {}

    lines: list[str] = []
    program_names: list[str] = []
    for target in targets.values():
        program_name = target.program.name
        if program_name not in program_names:
            program_names.append(program_name)
        own_trials = [row['response_score'] for row in trials if row['target_id'] == target.id]
        own_events = [row for row in behaviors if row['target_id'] == target.id]
        if own_trials:
            top = max_scores.get(target.id)
            correct = sum(1 for score in own_trials if (score >= top if top is not None else score > 0))
            result = f'{round(correct / len(own_trials) * 100)}% correct ({correct} of {len(own_trials)} trials)'
        elif own_events:
            timed = [row['duration_seconds'] for row in own_events if row['duration_seconds'] is not None]
            if timed:
                result = f'{len(timed)} timed occurrence{"s" if len(timed) != 1 else ""}, total {_hms(sum(timed))}'
            else:
                count = sum(row['frequency_count'] or 1 for row in own_events)
                result = f'{count} occurrence{"s" if count != 1 else ""}'
        else:
            continue
        lines.append(f'{program_name} — {target.name}: {result}')

    if not program_names:
        program_names = [p.get('name', '') for p in (session.program_snapshot or {}).get('programs', []) if p.get('name')]
    if program_names:
        out['program.names'] = ', '.join(program_names)
    if lines:
        out['program.targets_results'] = '\n'.join(lines)

    advanced = [
        f'{change.target.name}: {_label(change.from_status)} → {_label(change.to_status)}'
        for change in TargetStatusChange.objects.filter(
            session_run_id=session.id, trigger=TargetStatusChange.Trigger.AUTO_MASTERY,
        ).select_related('target')
    ]
    if advanced:
        out['program.targets_advanced'] = '\n'.join(advanced)

    faded = [
        f'{change.target.name}: {change.from_level_label} → {change.to_level_label}'
        for change in TargetPromptLevelChange.objects.filter(
            session_run_id=session.id, trigger=TargetPromptLevelChange.Trigger.AUTO_FADING,
        ).select_related('target')
    ]
    if faded:
        out['program.prompt_changes'] = '\n'.join(faded)
    return out


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

    # ── Programs (what was recorded in this session) ────────────────────────
    if session:
        out.update(resolve_program_tokens(session))

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


def apply_session_autofill(note: LessonNote, *, overwrite: bool) -> bool:
    """Fill a note's fields marked ``auto_fill`` from its session's recorded program data.

    On creation (``overwrite=False``) only empty fields are filled, so anything the author already
    typed stays. A refill (``overwrite=True``) replaces those fields with fresh session data.
    Returns True if the body changed."""
    template = note.template
    session = note.session_run
    if not (template and session and template.template_type == 'notes'):
        return False
    wanted = {f['key']: f['auto_fill'] for f in template.fields if f.get('auto_fill')}
    if not wanted:
        return False
    values = resolve_program_tokens(session)
    body = dict(note.body or {})
    changed = False
    for key, token in wanted.items():
        value = values.get(token)
        if not value:
            continue
        if not overwrite and body.get(key):
            continue
        if body.get(key) != value:
            body[key] = value
            changed = True
    if changed:
        note.body = body
        note.save(update_fields=['body', 'updated_at'])
    return changed


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
