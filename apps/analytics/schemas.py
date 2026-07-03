from datetime import date as Date, datetime
from ninja import Schema


class TrialDataPointSchema(Schema):
    date: Date
    target_id: int
    target_name: str
    total_trials: int
    correct_count: int
    pct_correct: float


class BehaviorDataPointSchema(Schema):
    date: Date
    target_id: int
    target_name: str
    frequency: int
    total_duration_seconds: int


class TargetSummarySchema(Schema):
    target_id: int
    target_name: str
    status: str
    total_trials: int
    total_sessions: int
    avg_pct_correct: float
    last_session_date: Date | None
    trend: str


class ProgramSummarySchema(Schema):
    program_id: int
    date_from: Date
    date_to: Date
    targets: list[TargetSummarySchema]


# ---------------------------------------------------------------------------
# Graph annotations
# ---------------------------------------------------------------------------

class GraphAnnotationSchema(Schema):
    id: int
    program_id: int
    target_id: int | None
    annotation_type: str
    date: Date
    end_date: Date | None
    label: str
    color: str
    style: str
    notes: str
    created_at: datetime


class GraphAnnotationCreateRequest(Schema):
    target_id: int | None = None
    annotation_type: str
    date: Date
    end_date: Date | None = None
    label: str
    color: str = '#666666'
    style: str = 'solid'
    notes: str = ''


class GraphAnnotationUpdateRequest(Schema):
    date: Date | None = None
    end_date: Date | None = None
    label: str | None = None
    color: str | None = None
    style: str | None = None
    notes: str | None = None
