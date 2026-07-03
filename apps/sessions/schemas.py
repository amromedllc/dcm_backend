from datetime import datetime
from typing import Any
from ninja import Schema


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

class AppointmentSchema(Schema):
    id: int
    client_id: int | None = None
    client_name: str | None = None
    staff_id: int | None
    staff_name: str | None = None
    lesson_id: int | None
    assigned_program_count: int = 0
    external_id: str
    source: str
    start_time: datetime
    end_time: datetime
    service_type: str
    location: str | None = None
    duration_minutes: int = 0
    notes: str
    status: str
    synced_at: datetime | None
    created_at: datetime


class AssignProgramsRequest(Schema):
    program_ids: list[int]
    client_id: int | None = None


class AssignedProgramSchema(Schema):
    id: int
    name: str
    category: str
    target_count: int = 0


class AppointmentCreateRequest(Schema):
    client_id: int
    staff_id: int
    lesson_id: int | None = None
    start_time: datetime
    end_time: datetime
    service_type: str = ''
    notes: str = ''


class AppointmentUpdateRequest(Schema):
    staff_id: int | None = None
    lesson_id: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    service_type: str | None = None
    notes: str | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class TrialSummaryItem(Schema):
    target_id: int
    target_name: str
    total_trials: int
    correct_count: int
    pct_correct: float


class SessionRunSchema(Schema):
    id: int
    client_id: int
    staff_id: int | None
    staff_name: str | None = None
    appointment_id: int | None
    appointment_start_time: datetime | None = None
    appointment_end_time: datetime | None = None
    lesson_id: int | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    rejection_reason: str
    program_snapshot: dict[str, Any]
    trial_summary: list[TrialSummaryItem] = []
    behavior_event_count: int = 0
    abc_event_count: int = 0
    created_at: datetime


class SessionStartRequest(Schema):
    client_id: int
    appointment_id: int | None = None
    lesson_id: int | None = None


class SessionSubmitRequest(Schema):
    ended_at: datetime | None = None


class TargetAdvancedSchema(Schema):
    name: str
    from_status: str
    to_status: str


class SessionSubmitResponse(Schema):
    session: SessionRunSchema
    advanced_targets: list[TargetAdvancedSchema] = []


class SessionRejectRequest(Schema):
    reason: str


# ---------------------------------------------------------------------------
# Trial events
# ---------------------------------------------------------------------------

class TrialEventSchema(Schema):
    id: int
    session_run_id: int
    target_id: int
    target_name: str
    trial_number: int
    response_score: int
    prompt_level_label: str
    recorded_at: datetime
    staff_notes: str


class TrialEventCreateRequest(Schema):
    target_id: int
    target_name: str
    trial_number: int
    response_score: int
    prompt_level_label: str
    recorded_at: datetime
    staff_notes: str = ''


# ---------------------------------------------------------------------------
# Behavior events
# ---------------------------------------------------------------------------

class BehaviorEventSchema(Schema):
    id: int
    session_run_id: int
    target_id: int
    target_name: str
    occurred_at: datetime
    duration_seconds: int | None
    frequency_count: int
    severity: str
    notes: str


class BehaviorEventCreateRequest(Schema):
    target_id: int
    target_name: str
    occurred_at: datetime
    duration_seconds: int | None = None
    frequency_count: int = 1
    severity: str = ''
    notes: str = ''


# ---------------------------------------------------------------------------
# ABC events
# ---------------------------------------------------------------------------

class ABCEventSchema(Schema):
    id: int
    session_run_id: int
    occurred_at: datetime
    antecedent: str
    behavior_description: str
    consequence: str
    setting: str
    staff_response: str
    notes: str


class ABCEventCreateRequest(Schema):
    occurred_at: datetime
    antecedent: str
    behavior_description: str
    consequence: str
    setting: str = ''
    staff_response: str = ''
    notes: str = ''


# ---------------------------------------------------------------------------
# Offline batch sync — mobile submits everything in one payload
# ---------------------------------------------------------------------------

class SessionSyncPayload(Schema):
    """
    Used by the mobile app after an offline session.
    All events are submitted in a single request when connectivity is restored.
    The API creates any events not already present (idempotent on trial_number per target).
    """
    ended_at: datetime | None = None
    trials: list[TrialEventCreateRequest] = []
    behaviors: list[BehaviorEventCreateRequest] = []
    abc: list[ABCEventCreateRequest] = []
    submit_after_sync: bool = False


class SessionSyncResult(Schema):
    trials_created: int
    behaviors_created: int
    abc_created: int
    submitted: bool
