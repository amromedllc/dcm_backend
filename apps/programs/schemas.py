from datetime import datetime
from typing import Any
from ninja import Schema


# ---------------------------------------------------------------------------
# Workflow templates
# ---------------------------------------------------------------------------

class WorkflowPhaseSchema(Schema):
    phase: str
    criteria: dict[str, Any] = {}
    on_success: str | None = None
    on_regression: str | None = None


class WorkflowTemplateSchema(Schema):
    id: int
    name: str
    description: str
    phases: list[dict[str, Any]]
    is_org_default: bool
    is_active: bool
    created_at: datetime


class WorkflowTemplateCreateRequest(Schema):
    name: str
    description: str = ''
    phases: list[dict[str, Any]]
    is_org_default: bool = False
    is_active: bool = True


class WorkflowTemplateUpdateRequest(Schema):
    name: str | None = None
    description: str | None = None
    phases: list[dict[str, Any]] | None = None
    is_org_default: bool | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# Maintenance schedules
# ---------------------------------------------------------------------------

class MaintenanceScheduleSchema(Schema):
    id: int
    name: str
    interval_type: str
    interval_value: int
    episodes: int
    success_threshold_pct: int
    on_failure: str
    is_org_default: bool
    created_at: datetime


class MaintenanceScheduleCreateRequest(Schema):
    name: str
    interval_type: str = 'every_n_sessions'
    interval_value: int = 5
    episodes: int = 4
    success_threshold_pct: int = 80
    on_failure: str = 'back_to_acquisition'
    is_org_default: bool = False


class MaintenanceScheduleUpdateRequest(Schema):
    name: str | None = None
    interval_type: str | None = None
    interval_value: int | None = None
    episodes: int | None = None
    success_threshold_pct: int | None = None
    on_failure: str | None = None
    is_org_default: bool | None = None


# ---------------------------------------------------------------------------
# Prompting templates
# ---------------------------------------------------------------------------

class PromptingLevelSchema(Schema):
    label: str
    score: int
    color: str
    abbreviation: str


class PromptingTemplateSchema(Schema):
    id: int
    name: str
    description: str
    levels: list[dict[str, Any]]
    is_org_default: bool
    created_at: datetime


class PromptingTemplateCreateRequest(Schema):
    name: str
    description: str = ''
    levels: list[dict[str, Any]]
    is_org_default: bool = False


class PromptingTemplateUpdateRequest(Schema):
    name: str | None = None
    description: str | None = None
    levels: list[dict[str, Any]] | None = None
    is_org_default: bool | None = None


# ---------------------------------------------------------------------------
# Mastery templates
# ---------------------------------------------------------------------------

class MasteryTemplateSchema(Schema):
    id: int
    name: str
    description: str
    rules: dict[str, Any]
    is_org_default: bool
    created_at: datetime


class MasteryTemplateCreateRequest(Schema):
    name: str
    description: str = ''
    rules: dict[str, Any]
    is_org_default: bool = False


class MasteryTemplateUpdateRequest(Schema):
    name: str | None = None
    description: str | None = None
    rules: dict[str, Any] | None = None
    is_org_default: bool | None = None


# ---------------------------------------------------------------------------
# Fading templates
# ---------------------------------------------------------------------------

class FadingTemplateSchema(Schema):
    id: int
    name: str
    description: str
    rules: dict[str, Any]
    is_org_default: bool
    created_at: datetime


class FadingTemplateCreateRequest(Schema):
    name: str
    description: str = ''
    rules: dict[str, Any]
    is_org_default: bool = False


class FadingTemplateUpdateRequest(Schema):
    name: str | None = None
    description: str | None = None
    rules: dict[str, Any] | None = None
    is_org_default: bool | None = None


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------

class TargetSummarySchema(Schema):
    id: int
    name: str
    status: str
    display_order: int
    is_visible_to_staff: bool


class ProgramSchema(Schema):
    id: int
    client_id: int
    name: str
    category: str
    status: str
    phase: str = 'teaching'
    treatment_area: str = ''
    tags: list[str] = []
    baseline_notes: str = ''
    objective: str = ''
    instructions: str = ''
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    display_order: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    targets: list[TargetSummarySchema] = []


class ProgramListSchema(Schema):
    id: int
    client_id: int
    name: str
    category: str
    status: str
    phase: str = 'teaching'
    treatment_area: str = ''
    tags: list[str] = []
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    display_order: int
    target_count: int = 0
    target_status_counts: dict[str, int] = {}
    created_at: datetime
    updated_at: datetime


class ProgramCreateRequest(Schema):
    client_id: int
    name: str
    category: str = 'skill_acquisition'
    phase: str = 'teaching'
    treatment_area: str = ''
    tags: list[str] = []
    baseline_notes: str = ''
    objective: str = ''
    instructions: str = ''
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    display_order: int = 0


class ProgramUpdateRequest(Schema):
    name: str | None = None
    category: str | None = None
    status: str | None = None
    phase: str | None = None
    treatment_area: str | None = None
    tags: list[str] | None = None
    baseline_notes: str | None = None
    objective: str | None = None
    instructions: str | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    display_order: int | None = None


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

class TargetSchema(Schema):
    id: int
    program_id: int
    name: str
    measurement_type: str
    sub_items: list[dict] = []
    prompting_template_id: int | None
    mastery_template_id: int | None
    workflow_template_id: int | None
    maintenance_schedule_id: int | None
    fading_template_id: int | None
    maintenance_episodes_completed: int
    sd_text: str
    teaching_instructions: str
    status: str
    mastery_mode: str
    fading_mode: str
    current_prompt_level_index: int
    display_order: int
    is_visible_to_staff: bool
    created_at: datetime
    updated_at: datetime


class TargetCreateRequest(Schema):
    name: str
    measurement_type: str = 'discrete_trial'
    sub_items: list[dict] = []
    prompting_template_id: int | None = None
    mastery_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    sd_text: str = ''
    teaching_instructions: str = ''
    status: str = ''  # empty = resolve server-side to the org's default TargetStatus
    display_order: int = 0
    is_visible_to_staff: bool = True


class TargetUpdateRequest(Schema):
    name: str | None = None
    measurement_type: str | None = None
    sub_items: list[dict] | None = None
    prompting_template_id: int | None = None
    mastery_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    sd_text: str | None = None
    teaching_instructions: str | None = None
    status: str | None = None
    mastery_mode: str | None = None
    fading_mode: str | None = None
    current_prompt_level_index: int | None = None
    display_order: int | None = None
    is_visible_to_staff: bool | None = None


class BulkUpdateTargetsRequest(Schema):
    """Update a specific subset of fields across multiple targets at once."""
    target_ids: list[int]
    # Only fields present (non-null) will be written — preserves other fields
    name: str | None = None
    mastery_mode: str | None = None
    fading_mode: str | None = None
    status: str | None = None
    measurement_type: str | None = None
    sd_text: str | None = None
    teaching_instructions: str | None = None
    prompting_template_id: int | None = None
    mastery_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    is_visible_to_staff: bool | None = None


class BulkUpdateResult(Schema):
    updated_count: int
    target_ids: list[int]


class ReorderTargetsRequest(Schema):
    ordered_ids: list[int]


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------

class LessonProgramSchema(Schema):
    id: int
    program_id: int
    program_name: str
    display_order: int


class LessonSchema(Schema):
    id: int
    client_id: int
    name: str
    lesson_type: str
    is_active: bool
    programs: list[LessonProgramSchema] = []
    created_at: datetime
    updated_at: datetime


class LessonCreateRequest(Schema):
    client_id: int
    name: str
    lesson_type: str = 'open'
    program_ids: list[int] = []


class LessonUpdateRequest(Schema):
    name: str | None = None
    lesson_type: str | None = None
    is_active: bool | None = None


class AddProgramToLessonRequest(Schema):
    program_id: int
    display_order: int = 0


# ---------------------------------------------------------------------------
# Org-level program templates (facility-wide library)
# ---------------------------------------------------------------------------

class OrgProgramSchema(Schema):
    id: int
    is_template: bool
    name: str
    category: str
    status: str
    phase: str
    treatment_area: str
    tags: list[str]
    objective: str
    instructions: str
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    display_order: int
    target_count: int = 0
    targets: list[TargetSummarySchema] = []
    created_at: datetime
    updated_at: datetime


class OrgProgramCreateRequest(Schema):
    name: str
    category: str = 'skill_acquisition'
    phase: str = 'teaching'
    treatment_area: str = ''
    tags: list[str] = []
    objective: str = ''
    instructions: str = ''
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    display_order: int = 0


class AssignOrgProgramRequest(Schema):
    client_id: int


# ---------------------------------------------------------------------------
# Treatment Areas
# ---------------------------------------------------------------------------

class TreatmentAreaSchema(Schema):
    id: int
    name: str
    description: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TreatmentAreaRequest(Schema):
    name: str
    description: str = ''
    is_active: bool = True


# ---------------------------------------------------------------------------
# Program Tags
# ---------------------------------------------------------------------------

class ProgramTagSchema(Schema):
    id: int
    name: str
    color: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProgramTagRequest(Schema):
    name: str
    color: str = '#6366f1'
    is_active: bool = True


# ---------------------------------------------------------------------------
# Target Statuses
# ---------------------------------------------------------------------------

class TargetStatusSchema(Schema):
    id: int
    key: str
    label: str
    color: str
    icon: str
    is_staff_visible: bool
    is_default: bool
    is_active: bool
    display_order: int
    created_at: datetime
    updated_at: datetime


class TargetStatusRequest(Schema):
    key: str
    label: str
    color: str = '#6366f1'
    icon: str = 'circle'
    is_staff_visible: bool = False
    is_default: bool = False
    is_active: bool = True
    display_order: int = 0


class TargetStatusUpdateRequest(Schema):
    label: str | None = None
    color: str | None = None
    icon: str | None = None
    is_staff_visible: bool | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    display_order: int | None = None


# ---------------------------------------------------------------------------
# Program Data Fields
# ---------------------------------------------------------------------------

class ProgramDataFieldSchema(Schema):
    id: int
    name: str
    field_type: str
    field_location: str
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProgramDataFieldRequest(Schema):
    name: str
    field_type: str = 'text'
    field_location: str = 'treatment_tab'
    display_order: int = 0
    is_active: bool = True


# ---------------------------------------------------------------------------
# Target audit history
# ---------------------------------------------------------------------------

class TargetStatusChangeSchema(Schema):
    id: int
    from_status: str
    to_status: str
    trigger: str
    session_run_id: int | None
    changed_by: str | None
    created_at: datetime


class TargetPromptLevelChangeSchema(Schema):
    id: int
    from_level_index: int
    to_level_index: int
    from_level_label: str
    to_level_label: str
    trigger: str
    session_run_id: int | None
    changed_by: str | None
    created_at: datetime
