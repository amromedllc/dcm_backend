"""AI-drafted programs: prompt, and strict validation of the model's output against what DCM allows.

Only the clinician's short description and the organization's own treatment-area and tag names are sent
to the model. No client information."""
from __future__ import annotations

import html
import re

from shared.ai_client import AIError, chat_json

MAX_DESCRIPTION_CHARS = 600
MAX_TARGETS = 15
MAX_NAME_CHARS = 200
MAX_TARGET_CHARS = 160
MAX_OBJECTIVE_CHARS = 500
MAX_INSTRUCTIONS_CHARS = 2000

# Only simple target types: the others need step lists or extra settings the draft doesn't carry.
ALLOWED_MEASUREMENT_TYPES = ('discrete_trial', 'duration', 'rate', 'frequency')
# Instructions Only can't hold targets; Telehealth is a delivery mode, not a teaching category.
ALLOWED_CATEGORIES = ('skill_acquisition', 'behavior_reduction', 'abc_recording', 'assessment')

SYSTEM_PROMPT_TEMPLATE = """You help behavior analysts draft a program for their own practice.
Reply with ONLY a JSON object, no other text, in exactly this shape:
{{"name": "program name", "category": "one of: {categories}",
 "treatment_area": "one of the treatment areas below, or empty",
 "tags": ["zero or more tags from the list below"],
 "objective": "one or two sentences",
 "instructions": "short plain-text teaching instructions, one step per line",
 "targets": [{{"name": "observable target", "measurement_type": "one of: {types}"}}]}}

Category guide: skill_acquisition = teaching new skills; behavior_reduction = decreasing a behavior;
abc_recording = antecedent-behavior-consequence data; assessment = a skills checklist.
Measurement guide: discrete_trial = scored attempts; duration = how long; rate = per minute; frequency = a count.

Treatment areas you may use: {areas}
Tags you may use: {tags}

Rules:
- Use 3 to 8 targets unless the request says otherwise. Each target is one short, observable statement.
- Write original, generic content. Do NOT copy, reproduce or closely paraphrase any published or commercial
  curriculum or assessment (VB-MAPP, ABLLS-R, AFLS, PEAK or similar).
- Do not include names, ages of real people, diagnoses of a specific person or any other personal information.
- This is a starting draft that a clinician will review, so keep it practical and neutral."""

_TAG_RE = re.compile(r'<[^>]+>')


def _clean(value, limit: int) -> str:
    text = _TAG_RE.sub('', str(value or '')).replace('\n', ' ').strip()
    return re.sub(r'\s+', ' ', text)[:limit]


def _plain_lines(value, limit: int) -> list[str]:
    text = _TAG_RE.sub('', str(value or ''))[:limit]
    return [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip()]


def _match(value, allowed: list[str]) -> str:
    """The organization's own spelling of a name, if the model's value matches one (case-insensitive)."""
    wanted = _clean(value, 200).lower()
    return next((item for item in allowed if item.lower() == wanted), '')


def normalize_program_draft(raw: dict, treatment_areas: list[str], tags: list[str]) -> dict:
    """Keep only values DCM accepts: known categories/types, and treatment areas and tags the org already has."""
    category = _clean(raw.get('category'), 40).lower()
    if category not in ALLOWED_CATEGORIES:
        category = 'skill_acquisition'

    targets: list[dict] = []
    seen: set[str] = set()
    for target in (raw.get('targets') or [])[:MAX_TARGETS * 2]:
        if not isinstance(target, dict):
            continue
        name = _clean(target.get('name'), MAX_TARGET_CHARS)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        measurement = _clean(target.get('measurement_type'), 40).lower()
        targets.append({
            'name': name,
            'measurement_type': measurement if measurement in ALLOWED_MEASUREMENT_TYPES else 'discrete_trial',
        })
        if len(targets) >= MAX_TARGETS:
            break

    name = _clean(raw.get('name'), MAX_NAME_CHARS)
    if not name and not targets:
        raise AIError('The AI did not return a usable program. Try describing it differently.')

    lines = _plain_lines(raw.get('instructions'), MAX_INSTRUCTIONS_CHARS)
    picked_tags: list[str] = []
    for tag in raw.get('tags') or []:
        match = _match(tag, tags)
        if match and match not in picked_tags:
            picked_tags.append(match)

    return {
        'name': name or 'Program (AI draft)',
        'category': category,
        'treatment_area': _match(raw.get('treatment_area'), treatment_areas),
        'tags': picked_tags,
        'objective': _clean(raw.get('objective'), MAX_OBJECTIVE_CHARS),
        'instructions_html': ''.join(f'<p>{html.escape(line)}</p>' for line in lines),
        'targets': targets,
    }


def generate_program_draft(description: str, treatment_areas: list[str], tags: list[str]) -> dict:
    description = _clean(description, MAX_DESCRIPTION_CHARS)
    if len(description) < 10:
        raise AIError('Describe the program you want in a few words (at least 10 characters).', status=400)
    system = SYSTEM_PROMPT_TEMPLATE.format(
        categories=', '.join(ALLOWED_CATEGORIES),
        types=', '.join(ALLOWED_MEASUREMENT_TYPES),
        areas=', '.join(treatment_areas) or '(none defined; leave empty)',
        tags=', '.join(tags) or '(none defined; leave empty)',
    )
    raw = chat_json(system, f'Draft a program for: {description}', max_tokens=3000)
    return normalize_program_draft(raw, treatment_areas, tags)
