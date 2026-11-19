from typing import Literal

from ninja import Schema
from pydantic import ConfigDict, Field


class PlannerNoteInput(Schema):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ObjectiveSelection(PlannerNoteInput):
    objective_ids: list[int] = Field(min_length=1, max_length=40)


class ApplyInput(ObjectiveSelection):
    field_key: str = Field(min_length=1, max_length=100)
    mode: Literal['fill', 'replace'] = 'fill'


class AddendumInput(PlannerNoteInput):
    text: str = Field(min_length=1, max_length=10000)


class HomeworkAssignInput(PlannerNoteInput):
    problem_component_id: str = Field(min_length=1, max_length=200)
    homework_component_id: str = Field(min_length=1, max_length=200)
    edition: str = Field(min_length=1, max_length=100)
    language: str = Field(default='en', min_length=1, max_length=20)
