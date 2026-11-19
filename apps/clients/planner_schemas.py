from datetime import date
from typing import Literal

from ninja import Schema
from pydantic import ConfigDict, Field


class PlannerInput(Schema):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class SourceInput(PlannerInput):
    component_id: str = Field(min_length=1, max_length=200)
    edition: str = Field(min_length=1, max_length=100)
    language: str = Field(default='en', min_length=1, max_length=20)


class LookupInput(PlannerInput):
    system: Literal['ICD-10-CM', 'DSM-5', 'SYNTHETIC']
    version: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=80)
    edition: str = Field(min_length=1, max_length=100)
    language: str = Field(default='en', min_length=1, max_length=20)
    cursor: str | None = Field(default=None, max_length=200)


class ChildrenInput(SourceInput):
    cursor: str | None = Field(default=None, max_length=200)


class RowInput(PlannerInput):
    id: int | None = Field(default=None, ge=1)
    source: SourceInput
    clinician_text: str = Field(default='', max_length=10000)


class InterventionInput(RowInput):
    pass


class ObjectiveInput(RowInput):
    interventions: list[InterventionInput] = Field(default_factory=list, max_length=40)


class GoalInput(RowInput):
    objectives: list[ObjectiveInput] = Field(default_factory=list, max_length=40)


class ProblemInput(RowInput):
    goals: list[GoalInput] = Field(default_factory=list, max_length=30)


class DraftInput(PlannerInput):
    title: str = Field(min_length=1, max_length=220)
    plan_date: date
    date_from: date | None = None
    date_to: date | None = None
    diagnosis_ids: list[int] = Field(default_factory=list, max_length=30)
    problems: list[ProblemInput] = Field(default_factory=list, max_length=20)


class DraftUpdate(DraftInput):
    expected_lock_version: int = Field(ge=1)


class DraftDelete(PlannerInput):
    expected_lock_version: int = Field(ge=1)


class DiagnosisInput(PlannerInput):
    code_system: Literal['ICD-10-CM', 'DSM-5', 'SYNTHETIC']
    code_version: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=80)
    display: str = Field(min_length=1, max_length=500)
    effective_from: date | None = None
    effective_to: date | None = None
    supersedes_id: int | None = Field(default=None, ge=1)


class DiagnosisUpdate(PlannerInput):
    expected_lock_version: int = Field(ge=1)
    clinical_status: Literal['active', 'inactive', 'resolved']
    effective_to: date | None = None


class FinalizeInput(PlannerInput):
    expected_lock_version: int = Field(ge=1)


class SignInput(PlannerInput):
    expected_lock_version: int = Field(ge=1)
    signature_data: str = Field(min_length=1, max_length=200)
    attested: bool


class ReviseInput(PlannerInput):
    expected_lock_version: int = Field(ge=1)


class ActivationInput(PlannerInput):
    expected_lock_version: int = Field(ge=1)
    effective_from: date
    effective_to: date | None = None
