"""Our normalized contract, not a claim about Wiley's private API schema.

Provider inputs deliberately exclude clients, patient identifiers and narratives.
"""
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Category(StrEnum):
    PROBLEM = 'problem'
    GOAL = 'goal'
    OBJECTIVE = 'objective'
    INTERVENTION = 'intervention'
    PROGRESS_NOTE = 'progress_note'
    HOMEWORK = 'homework'


class PlannerError(Exception):
    """Safe public error; never interpolate vendor response bodies or queries."""


class PlannerUnavailable(PlannerError):
    pass


class ContentNotFound(PlannerError):
    pass


@dataclass(frozen=True)
class DiagnosisQuery:
    system: str
    version: str
    code: str

    def __post_init__(self):
        if self.system not in ('ICD-10-CM', 'DSM-5', 'SYNTHETIC'):
            raise ValueError('Explicit supported diagnosis system required')
        if not self.version.strip() or not self.code.strip():
            raise ValueError('Diagnosis version and code required')


@dataclass(frozen=True)
class Content:
    provider: str
    planner_id: str
    component_id: str
    category: Category
    text: str
    edition: str
    version: str
    language: str
    attribution: str
    parent_ids: tuple[str, ...] = ()
    retired: bool = False

    def __post_init__(self):
        if not isinstance(self.category, Category):
            raise ValueError('Invalid content category')
        for field in ('provider', 'planner_id', 'component_id', 'text', 'edition', 'version', 'language', 'attribution'):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Content identity, wording and attribution required')
        if not isinstance(self.parent_ids, tuple) or any(not isinstance(p, str) or not p for p in self.parent_ids):
            raise ValueError('Immutable parent identifiers required')
        if not isinstance(self.retired, bool):
            raise ValueError('Explicit retirement flag required')


@dataclass(frozen=True)
class ContentPage:
    items: tuple[Content, ...]
    next_cursor: str | None = None


class PracticePlannerProvider(Protocol):
    provider_id: str

    def lookup_diagnosis(self, query: DiagnosisQuery, *, edition: str, language: str = 'en', cursor: str | None = None) -> ContentPage: ...

    def get_component(self, component_id: str, *, edition: str, language: str = 'en') -> Content: ...

    def children(self, component_id: str, *, edition: str, language: str = 'en', cursor: str | None = None) -> ContentPage: ...
