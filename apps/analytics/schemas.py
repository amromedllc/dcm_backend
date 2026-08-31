from datetime import date as Date, datetime
from ninja import Schema


class TrialDataPointSchema(Schema):
    date: Date
    target_id: int | str
    target_name: str
    module_id: int | None
    module_name: str | None
    submodule_id: int | None
    submodule_name: str | None
    total_trials: int
    correct_count: int
    pct_correct: float
    duration_seconds: float


class BehaviorDataPointSchema(Schema):
    date: Date
    target_id: int
    target_name: str
    module_id: int | None
    module_name: str | None
    submodule_id: int | None
    submodule_name: str | None
    frequency: int
    total_duration_seconds: int
    min_duration_seconds: float = 0.0
    max_duration_seconds: float = 0.0
    avg_duration_seconds: float = 0.0
    measurement: str = ''
    measurement_value: float = 0.0
    measurement_label: str = 'Frequency'
    measurement_unit: str = 'count'


class TargetSummarySchema(Schema):
    target_id: int
    target_name: str
    status: str
    phase: str | None
    module_id: int | None
    module_name: str | None
    submodule_id: int | None
    submodule_name: str | None
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
    # % correct threshold a target must sustain to reach 'mastered', read from
    # the targets' WorkflowTemplates. None when no target defines one.
    # `mastery_criteria_varies` is True when targets disagree and `pct` is the
    # most common value.
    mastery_criteria_pct: int | None = None
    mastery_criteria_varies: bool = False


class ModuleSummarySchema(Schema):
    module_id: int
    module_name: str
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


# ---------------------------------------------------------------------------
# Client-level annotations (Progress screen mastery chart)
# ---------------------------------------------------------------------------

class ClientAnnotationSchema(Schema):
    id: int
    external_client_id: int
    date: Date
    label: str
    color: str
    style: str
    notes: str
    created_at: datetime


class ClientAnnotationCreateRequest(Schema):
    date: Date
    label: str
    color: str = '#666666'
    style: str = 'solid'
    notes: str = ''


class ClientAnnotationUpdateRequest(Schema):
    date: Date | None = None
    label: str | None = None
    color: str | None = None
    style: str | None = None
    notes: str | None = None
