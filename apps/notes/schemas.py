from datetime import date, datetime
from typing import Any
from ninja import Schema


# ---------------------------------------------------------------------------
# Note templates
# ---------------------------------------------------------------------------

class NoteTemplateFieldSchema(Schema):
    key: str
    label: str
    type: str                         # text | textarea | number | boolean | select | multiselect | date
    required: bool = False
    placeholder: str = ''
    options: list[str] = []           # for select / multiselect


class NoteTemplateSchema(Schema):
    id: int
    name: str
    description: str
    fields: list[dict[str, Any]]
    is_org_default: bool
    is_active: bool
    created_at: datetime


class NoteTemplateCreateRequest(Schema):
    name: str
    description: str = ''
    fields: list[dict[str, Any]]
    is_org_default: bool = False


class NoteTemplateUpdateRequest(Schema):
    name: str | None = None
    description: str | None = None
    fields: list[dict[str, Any]] | None = None
    is_org_default: bool | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# Note signatures
# ---------------------------------------------------------------------------

class NoteSignatureSchema(Schema):
    id: int
    note_id: int
    signer_id: int
    signer_name: str
    signer_role: str
    signature_type: str
    signed_at: datetime


class SignNoteRequest(Schema):
    signature_type: str                 # staff | supervisor | caregiver
    signature_data: str = ''            # base64 image or text acknowledgement
    ip_address_hash: str = ''


# ---------------------------------------------------------------------------
# Lesson notes
# ---------------------------------------------------------------------------

class LessonNoteSchema(Schema):
    id: int
    client_id: int
    session_run_id: int | None
    staff_id: int | None
    template_id: int | None
    note_date: date
    body: dict[str, Any]
    status: str
    submitted_at: datetime | None
    approved_by_id: int | None
    approved_at: datetime | None
    rejected_by_id: int | None
    rejected_at: datetime | None
    rejection_reason: str
    requires_caregiver_signature: bool
    signatures: list[NoteSignatureSchema] = []
    created_at: datetime
    updated_at: datetime


class LessonNoteListSchema(Schema):
    id: int
    client_id: int
    session_run_id: int | None
    staff_id: int | None
    staff_name: str | None = None
    template_id: int | None
    template_name: str | None = None
    note_date: date
    status: str
    submitted_at: datetime | None
    requires_caregiver_signature: bool
    signature_count: int = 0
    created_at: datetime
    updated_at: datetime


class NoteCreateRequest(Schema):
    client_id: int
    session_run_id: int | None = None
    template_id: int | None = None
    assignment_id: int | None = None      # if filling an assigned form
    note_date: date
    body: dict[str, Any] = {}
    requires_caregiver_signature: bool = False


# ---------------------------------------------------------------------------
# Note assignments
# ---------------------------------------------------------------------------

class NoteAssignmentSchema(Schema):
    id: int
    tpms_appointment_id: int
    tpms_client_id: int | None
    template_id: int
    template_name: str
    is_filled: bool
    note_id: int | None
    assigned_by_name: str | None
    created_at: datetime


class NoteAssignmentCreateRequest(Schema):
    tpms_appointment_id: int
    template_id: int
    tpms_client_id: int | None = None


class NoteUpdateRequest(Schema):
    note_date: date | None = None
    body: dict[str, Any] | None = None
    requires_caregiver_signature: bool | None = None


class NoteRejectRequest(Schema):
    reason: str


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

class ReviewQueueItem(Schema):
    id: int
    client_id: int
    staff_id: int | None
    note_date: date
    submitted_at: datetime | None
    template_name: str | None
    session_run_id: int | None
