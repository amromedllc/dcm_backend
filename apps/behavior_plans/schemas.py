from datetime import date, datetime
from typing import Literal
from ninja import Schema

from shared.schema_types import NonEmptyStr

FunctionLiteral = Literal['escape', 'attention', 'tangible', 'sensory', 'multiple', 'unknown']

CONTENT_FIELD_NAMES = (
    'title',
    'target_behavior_name',
    'target_behavior_definition',
    'baseline_summary',
    'hypothesized_function',
    'function_hypothesis_statement',
    'antecedent_strategies',
    'replacement_behaviors',
    'teaching_procedures',
    'reinforcement_strategies',
    'response_procedures',
    'crisis_plan',
    'safety_precautions',
    'data_collection_plan',
    'generalization_plan',
)


class BipVersionListSchema(Schema):
    id: int
    client_id: int
    program_id: int
    program_name: str
    version_number: int
    previous_version_id: int | None
    title: str
    target_behavior_name: str
    hypothesized_function: str
    status: str
    author_id: int | None
    author_name: str
    effective_date: date | None
    review_due_date: date | None
    submitted_at: datetime | None
    activated_by_name: str
    activated_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BipVersionSchema(BipVersionListSchema):
    target_behavior_definition: str
    baseline_summary: str
    function_hypothesis_statement: str
    antecedent_strategies: str
    replacement_behaviors: str
    teaching_procedures: str
    reinforcement_strategies: str
    response_procedures: str
    crisis_plan: str
    safety_precautions: str
    data_collection_plan: str
    generalization_plan: str
    rejected_by_id: int | None
    rejected_at: datetime | None
    rejection_reason: str
    archived_by_id: int | None
    archive_reason: str
    next_version_id: int | None


class BipCreateRequest(Schema):
    client_id: int
    program_id: int
    title: str = ''
    target_behavior_name: NonEmptyStr
    target_behavior_definition: str = ''
    baseline_summary: str = ''
    hypothesized_function: FunctionLiteral = 'unknown'
    function_hypothesis_statement: str = ''
    antecedent_strategies: str = ''
    replacement_behaviors: str = ''
    teaching_procedures: str = ''
    reinforcement_strategies: str = ''
    response_procedures: str = ''
    crisis_plan: str = ''
    safety_precautions: str = ''
    data_collection_plan: str = ''
    generalization_plan: str = ''
    effective_date: date | None = None
    review_due_date: date | None = None


class BipUpdateRequest(Schema):
    title: str | None = None
    target_behavior_name: str | None = None
    target_behavior_definition: str | None = None
    baseline_summary: str | None = None
    hypothesized_function: FunctionLiteral | None = None
    function_hypothesis_statement: str | None = None
    antecedent_strategies: str | None = None
    replacement_behaviors: str | None = None
    teaching_procedures: str | None = None
    reinforcement_strategies: str | None = None
    response_procedures: str | None = None
    crisis_plan: str | None = None
    safety_precautions: str | None = None
    data_collection_plan: str | None = None
    generalization_plan: str | None = None
    effective_date: date | None = None
    review_due_date: date | None = None


class BipReviseRequest(Schema):
    title: str | None = None


class BipRejectRequest(Schema):
    reason: NonEmptyStr


class BipArchiveRequest(Schema):
    reason: str = ''


class BipActivateRequest(Schema):
    effective_date: date | None = None
    review_due_date: date | None = None
