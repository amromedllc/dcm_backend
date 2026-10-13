"""AI-drafted skills assessments: prompt, and strict clean-up of whatever the model returns.

The model only ever sees the clinician's short description of the assessment they want.
No client information is sent."""
from __future__ import annotations

import re

from shared.ai_client import AIError, chat_json

MAX_DESCRIPTION_CHARS = 600
MAX_AREAS = 12
MAX_SKILLS_PER_AREA = 15
MAX_NAME_CHARS = 160
MAX_AREA_CHARS = 80
MAX_OBJECTIVE_CHARS = 500

SYSTEM_PROMPT = """You help behavior analysts draft a blank skills assessment for their own practice.
Reply with ONLY a JSON object, no other text, in exactly this shape:
{"name": "short assessment title", "objective": "one sentence purpose",
 "areas": [{"name": "area name", "skills": ["skill statement", "..."]}]}

Rules:
- Use 4 to 8 areas and 4 to 10 skills per area, unless the request says otherwise.
- Each skill is one short, observable, plain-language statement (for example "Asks for a preferred item using a word or sign").
- Write original, generic content. Do NOT copy, reproduce or closely paraphrase any published or commercial
  assessment such as the VB-MAPP, ABLLS-R, AFLS, PEAK, Vineland or any other copyrighted protocol.
- Do not include any names, ages of real people, diagnoses of a specific person or other personal information.
- The practice will review and edit everything, so keep it neutral and practical."""

_TAG_RE = re.compile(r'<[^>]+>')


def _clean(value, limit: int) -> str:
    text = _TAG_RE.sub('', str(value or '')).replace('\n', ' ').strip()
    return re.sub(r'\s+', ' ', text)[:limit]


def normalize_assessment_draft(raw: dict) -> dict:
    """Trim, de-duplicate and cap a draft so it is safe to show and save. Raises AIError if nothing usable."""
    areas = []
    for area in (raw.get('areas') or [])[:MAX_AREAS]:
        if not isinstance(area, dict):
            continue
        name = _clean(area.get('name'), MAX_AREA_CHARS)
        skills: list[str] = []
        for skill in (area.get('skills') or [])[:MAX_SKILLS_PER_AREA * 2]:
            text = _clean(skill, MAX_NAME_CHARS)
            if text and text.lower() not in {s.lower() for s in skills}:
                skills.append(text)
            if len(skills) >= MAX_SKILLS_PER_AREA:
                break
        if name and skills:
            areas.append({'name': name, 'skills': skills})
    if not areas:
        raise AIError('The AI did not return a usable assessment. Try describing it differently.')
    return {
        'name': _clean(raw.get('name'), MAX_NAME_CHARS) or 'Skills Assessment (AI draft)',
        'objective': _clean(raw.get('objective'), MAX_OBJECTIVE_CHARS),
        'areas': areas,
    }


def generate_assessment_draft(description: str) -> dict:
    description = _clean(description, MAX_DESCRIPTION_CHARS)
    if len(description) < 10:
        raise AIError('Describe the assessment you want in a few words (at least 10 characters).', status=400)
    raw = chat_json(SYSTEM_PROMPT, f'Draft an assessment for: {description}', max_tokens=3000)
    return normalize_assessment_draft(raw)
