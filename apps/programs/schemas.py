from datetime import datetime
from typing import Any
from ninja import Schema
from pydantic import Field, field_validator

from shared.schema_types import NonEmptyStr, SlugStr
from .models import Program, Target, MaintenanceSchedule, ProgramDataField, Lesson


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


def _require_phase_keys(phases: list[dict] | None) -> list[dict] | None:
    if phases is None:
        return None
    for p in phases:
        if not isinstance(p, dict) or not str(p.get('phase') or '').strip():
            raise ValueError('each phase must be an object with a non-empty "phase" key')
    return phases


class WorkflowTemplateCreateRequest(Schema):
    name: NonEmptyStr
    description: str = ''
    phases: list[dict[str, Any]] = Field(min_length=1)
    is_org_default: bool = False
    is_active: bool = True

    @field_validator('phases')
    @classmethod
    def _check_phases(cls, v):
        return _require_phase_keys(v)


class WorkflowTemplateUpdateRequest(Schema):
    name: NonEmptyStr | None = None
    description: str | None = None
    phases: list[dict[str, Any]] | None = Field(default=None, min_length=1)
    is_org_default: bool | None = None
    is_active: bool | None = None

    @field_validator('phases')
    @classmethod
    def _check_phases(cls, v):
        return _require_phase_keys(v)


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
    name: NonEmptyStr
    interval_type: MaintenanceSchedule.IntervalType = MaintenanceSchedule.IntervalType.EVERY_N_SESSIONS
    interval_value: int = Field(default=5, ge=1)
    episodes: int = Field(default=4, ge=1)
    success_threshold_pct: int = Field(default=80, ge=0, le=100)
    on_failure: MaintenanceSchedule.OnFailure = MaintenanceSchedule.OnFailure.BACK_TO_ACQUISITION
    is_org_default: bool = False


class MaintenanceScheduleUpdateRequest(Schema):
    name: NonEmptyStr | None = None
    interval_type: MaintenanceSchedule.IntervalType | None = None
    interval_value: int | None = Field(default=None, ge=1)
    episodes: int | None = Field(default=None, ge=1)
    success_threshold_pct: int | None = Field(default=None, ge=0, le=100)
    on_failure: MaintenanceSchedule.OnFailure | None = None
    is_org_default: bool | None = None


# ---------------------------------------------------------------------------
# Prompting templates
# ---------------------------------------------------------------------------

class PromptingLevelSchema(Schema):
    label: NonEmptyStr
    # No longer client-supplied — _normalize_levels() below derives it from
    # is_success (1 for the success level, 0 for every other) on every save,
    # regardless of what's sent. Kept as a field (rather than dropped) because
    # existing stored levels and TrialEvent.response_score still key off it.
    score: int = 0
    color: str
    abbreviation: str
    is_success: bool = False


class PromptingTemplateSchema(Schema):
    id: int
    name: str
    description: str
    levels: list[dict[str, Any]]
    is_org_default: bool
    created_at: datetime


def _normalize_levels(levels: list[PromptingLevelSchema] | None) -> list[PromptingLevelSchema] | None:
    """Exactly one level must be marked is_success (there's no separate score
    field anymore for admins to fall back on to signal "this one is correct").
    Normalizes score to the binary value everything downstream already
    effectively treats it as: 1 for the success level, 0 for every other."""
    if levels is None:
        return None
    success_count = sum(1 for lvl in levels if lvl.is_success)
    if success_count == 0:
        raise ValueError('Mark one prompt level as the successful outcome')
    if success_count > 1:
        raise ValueError('Only one prompt level can be marked as the successful outcome')
    for lvl in levels:
        lvl.score = 1 if lvl.is_success else 0
    return levels


class PromptingTemplateCreateRequest(Schema):
    name: NonEmptyStr
    description: str = ''
    levels: list[PromptingLevelSchema] = Field(min_length=1)
    is_org_default: bool = False

    @field_validator('levels')
    @classmethod
    def _check_levels(cls, v):
        return _normalize_levels(v)


class PromptingTemplateUpdateRequest(Schema):
    name: NonEmptyStr | None = None
    description: str | None = None
    levels: list[PromptingLevelSchema] | None = Field(default=None, min_length=1)
    is_org_default: bool | None = None

    @field_validator('levels')
    @classmethod
    def _check_levels(cls, v):
        return _normalize_levels(v)


# ---------------------------------------------------------------------------
# Fading templates
# ---------------------------------------------------------------------------

class FadingRulesSchema(Schema):
    """Mirrors the .get(key, default) reads in services.py's fading logic —
    every key is optional with the same default, so validating this shape
    can't change behavior for a caller that omits a key."""
    threshold_pct: int = Field(default=90, ge=0, le=100)
    consecutive_sessions: int = Field(default=3, ge=1)
    minimum_trials: int = Field(default=5, ge=1)
    regression_threshold_pct: int = Field(default=50, ge=0, le=100)


class FadingTemplateSchema(Schema):
    id: int
    name: str
    description: str
    rules: dict[str, Any]
    is_org_default: bool
    created_at: datetime


class FadingTemplateCreateRequest(Schema):
    name: NonEmptyStr
    description: str = ''
    rules: dict[str, Any]
    is_org_default: bool = False

    @field_validator('rules')
    @classmethod
    def _validate_rules(cls, v):
        return FadingRulesSchema(**v).dict()


class FadingTemplateUpdateRequest(Schema):
    name: NonEmptyStr | None = None
    description: str | None = None
    rules: dict[str, Any] | None = None
    is_org_default: bool | None = None

    @field_validator('rules')
    @classmethod
    def _validate_rules(cls, v):
        return FadingRulesSchema(**v).dict() if v is not None else v


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------

class TargetSummarySchema(Schema):
    id: int
    name: str
    status: str
    measurement_type: str = ''
    display_order: int
    is_visible_to_staff: bool
    # Central Library targets only — whether this target is already copied
    # into the caller's linked org program.
    already_copied: bool = False


class ProgramMaterialSchema(Schema):
    id: int
    program_id: int
    title: str
    material_type: str
    file_url: str
    content_type: str
    file_size: int
    uploaded_by: str | None = None
    created_at: datetime


class ProgramSchema(Schema):
    id: int
    client_id: int | None = None
    name: str
    category: str
    status: str
    phase: str = 'teaching'
    treatment_area: str = ''
    tags: list[str] = []
    baseline_notes: str = ''
    objective: str = ''
    instructions: str = ''
    prompting_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    image_url: str | None = None
    display_order: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    targets: list[TargetSummarySchema] = []
    materials: list[ProgramMaterialSchema] = []


class ProgramListSchema(Schema):
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
    prompting_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    image_url: str | None = None
    display_order: int
    target_count: int = 0
    target_status_counts: dict[str, int] = {}
    created_at: datetime
    updated_at: datetime


class ProgramCreateRequest(Schema):
    client_id: int
    name: NonEmptyStr
    category: Program.Category = Program.Category.SKILL_ACQUISITION
    phase: Program.Phase = Program.Phase.TEACHING
    treatment_area: str = ''
    tags: list[str] = []
    baseline_notes: str = ''
    objective: str = ''
    instructions: str = ''
    prompting_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    display_order: int = Field(default=0, ge=0)


class ProgramUpdateRequest(Schema):
    name: NonEmptyStr | None = None
    category: Program.Category | None = None
    status: Program.Status | None = None
    phase: Program.Phase | None = None
    treatment_area: str | None = None
    tags: list[str] | None = None
    baseline_notes: str | None = None
    objective: str | None = None
    instructions: str | None = None
    prompting_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    display_order: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Modules / Submodules
# ---------------------------------------------------------------------------

class ProgramSubmoduleSchema(Schema):
    id: int
    module_id: int
    name: str
    display_order: int
    target_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProgramModuleSchema(Schema):
    id: int
    program_id: int
    name: str
    display_order: int
    target_count: int = 0
    submodules: list[ProgramSubmoduleSchema] = []
    created_at: datetime
    updated_at: datetime


class ProgramModuleRequest(Schema):
    name: NonEmptyStr
    display_order: int = Field(default=0, ge=0)


class ProgramSubmoduleRequest(Schema):
    name: NonEmptyStr
    display_order: int = Field(default=0, ge=0)


class TargetSchema(Schema):
    id: int
    program_id: int
    name: str
    measurement_type: str
    sub_items: list[dict] = []
    sub_item_progression: str = 'forward'
    prompting_template_id: int | None
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
    module_id: int | None = None
    submodule_id: int | None = None
    created_at: datetime
    updated_at: datetime

# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
class TargetCreateRequest(Schema):
    name: NonEmptyStr
    measurement_type: Target.MeasurementType = Target.MeasurementType.DISCRETE_TRIAL
    sub_items: list[dict] = []
    sub_item_progression: Target.SubItemProgression = Target.SubItemProgression.FORWARD
    prompting_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    sd_text: str = ''
    teaching_instructions: str = ''
    status: str = ''  # empty = resolve server-side to the org's default TargetStatus
    mastery_mode: Target.MasteryMode = Target.MasteryMode.MANUAL
    fading_mode: Target.FadingMode = Target.FadingMode.MANUAL
    display_order: int = Field(default=0, ge=0)
    is_visible_to_staff: bool = True
    module_id: int | None = None
    submodule_id: int | None = None


class TargetUpdateRequest(Schema):
    name: NonEmptyStr | None = None
    measurement_type: Target.MeasurementType | None = None
    sub_items: list[dict] | None = None
    sub_item_progression: Target.SubItemProgression | None = None
    prompting_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    sd_text: str | None = None
    teaching_instructions: str | None = None
    status: str | None = None
    mastery_mode: Target.MasteryMode | None = None
    fading_mode: Target.FadingMode | None = None
    current_prompt_level_index: int | None = Field(default=None, ge=0)
    display_order: int | None = Field(default=None, ge=0)
    is_visible_to_staff: bool | None = None
    module_id: int | None = None
    submodule_id: int | None = None


class BulkUpdateTargetsRequest(Schema):
    """Update a specific subset of fields across multiple targets at once."""
    target_ids: list[int]
    # Only fields present (non-null) will be written — preserves other fields
    name: NonEmptyStr | None = None
    mastery_mode: Target.MasteryMode | None = None
    fading_mode: Target.FadingMode | None = None
    status: str | None = None
    measurement_type: Target.MeasurementType | None = None
    sd_text: str | None = None
    teaching_instructions: str | None = None
    prompting_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    is_visible_to_staff: bool | None = None


class BulkUpdateResult(Schema):
    updated_count: int
    target_ids: list[int]


class ReorderTargetsRequest(Schema):
    ordered_ids: list[int]


class ReorderModulesRequest(Schema):
    ordered_ids: list[int]


class ReorderSubmodulesRequest(Schema):
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
    name: NonEmptyStr
    lesson_type: Lesson.LessonType = Lesson.LessonType.OPEN
    program_ids: list[int] = []


class LessonUpdateRequest(Schema):
    name: NonEmptyStr | None = None
    lesson_type: Lesson.LessonType | None = None
    is_active: bool | None = None


class AddProgramToLessonRequest(Schema):
    program_id: int
    display_order: int = Field(default=0, ge=0)


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
    prompting_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    folder_id: int | None = None
    image_url: str | None = None
    already_imported: bool = False
    # Central Library programs only — how many of target_count are already
    # copied into the caller's linked org program (0 for org's own programs).
    imported_target_count: int = 0
    display_order: int
    target_count: int = 0
    targets: list[TargetSummarySchema] = []
    created_at: datetime
    updated_at: datetime


class OrgProgramCreateRequest(Schema):
    name: NonEmptyStr
    category: Program.Category = Program.Category.SKILL_ACQUISITION
    phase: Program.Phase = Program.Phase.TEACHING
    treatment_area: str = ''
    tags: list[str] = []
    objective: str = ''
    instructions: str = ''
    prompting_template_id: int | None = None
    workflow_template_id: int | None = None
    maintenance_schedule_id: int | None = None
    fading_template_id: int | None = None
    display_order: int = Field(default=0, ge=0)


class AssignOrgProgramRequest(Schema):
    client_id: int


# ---------------------------------------------------------------------------
# Org program folders (Program Management → Organization tab)
# ---------------------------------------------------------------------------

class ProgramFolderSchema(Schema):
    id: int
    name: str
    display_order: int
    program_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProgramFolderRequest(Schema):
    name: NonEmptyStr
    display_order: int = Field(default=0, ge=0)


class SetProgramFolderRequest(Schema):
    folder_id: int | None = None


# ---------------------------------------------------------------------------
# Saved table views (Programs table — filters/group-by/columns)
# ---------------------------------------------------------------------------

class SavedTableViewSchema(Schema):
    id: int
    table_key: str
    name: str
    config: dict
    visibility: str
    roles: list[str]
    display_order: int
    created_by_id: int | None = None
    is_mine: bool = False
    created_at: datetime
    updated_at: datetime


class SavedTableViewCreateRequest(Schema):
    table_key: NonEmptyStr
    name: NonEmptyStr
    config: dict = {}
    visibility: str = 'private'
    roles: list[str] = []
    display_order: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Central Library folders (platform-owned, read-only for orgs)
# ---------------------------------------------------------------------------

class CentralProgramFolderSchema(Schema):
    id: int
    name: str
    display_order: int
    program_count: int = 0


class ImportCentralFolderResult(Schema):
    folder_id: int
    folder_name: str
    imported_count: int
    skipped_count: int


class KnowledgeBaseTopicSchema(Schema):
    id: int
    title: str
    summary: str
    items: list[str]
    display_order: int


class KnowledgeBaseModuleSchema(Schema):
    id: int
    slug: str
    title: str
    path: str
    icon: str
    overview: str
    audience: list[str]
    display_order: int
    updated_at: datetime
    topics: list[KnowledgeBaseTopicSchema]


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
    name: NonEmptyStr
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
    name: NonEmptyStr
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
    key: SlugStr
    label: NonEmptyStr
    color: str = '#6366f1'
    icon: str = 'circle'
    is_staff_visible: bool = False
    is_default: bool = False
    is_active: bool = True
    display_order: int = Field(default=0, ge=0)


class TargetStatusUpdateRequest(Schema):
    label: NonEmptyStr | None = None
    color: str | None = None
    icon: str | None = None
    is_staff_visible: bool | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    display_order: int | None = Field(default=None, ge=0)


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
    name: NonEmptyStr
    field_type: ProgramDataField.FieldType = ProgramDataField.FieldType.TEXT
    field_location: ProgramDataField.FieldLocation = ProgramDataField.FieldLocation.TREATMENT_TAB
    display_order: int = Field(default=0, ge=0)
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
