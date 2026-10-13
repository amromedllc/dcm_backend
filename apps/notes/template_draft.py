"""AI-drafted note templates based on a library program: prompt, and strict validation of the output.

Only the program's own name, category, objective and target names (library programs never hold client
information) plus an optional short instruction from the person are sent to the model."""
from __future__ import annotations

import re

from shared.ai_client import AIError, chat_json

MAX_NOTE_CHARS = 400
MAX_FIELDS = 12
MAX_TARGETS_SENT = 20
FIELD_TYPES = ('text', 'textarea', 'number', 'boolean', 'select', 'multiselect', 'date')

SYSTEM_PROMPT = """You help behavior analysts draft a session note template for a program in their own practice.
Reply with ONLY a JSON object, no other text, in exactly this shape:
{{"name": "template name", "description": "one sentence",
 "fields": [{{"key": "snake_case_key", "label": "Field label", "type": "one of: {types}",
   "required": true, "placeholder": "short hint or empty", "options": ["only for select / multiselect"]}}]}}

Rules:
- Use 5 to 9 fields that a clinician fills in after running this program: for example session summary,
  how the client responded, prompt levels used, target-specific observations, challenging behavior,
  caregiver communication and plan for next session. Tailor them to the program and its targets.
- Use textarea for narrative, select for a short fixed set of choices, boolean for yes/no.
- Do NOT include fields for data the system already records (session date, times, staff name, target scores).
- Write generic content. Do not include names or any personal information.
- This is a starting draft that a clinician will review, so keep it practical and neutral."""

_TAG_RE = re.compile(r'<[^>]+>')


def _clean(value, limit: int) -> str:
    text = _TAG_RE.sub('', str(value or '')).replace('\n', ' ').strip()
    return re.sub(r'\s+', ' ', text)[:limit]


def _key(value, fallback: str) -> str:
    key = re.sub(r'[^a-z0-9]+', '_', _clean(value, 60).lower()).strip('_')
    return key or fallback


def normalize_template_draft(raw: dict) -> dict:
    """Keep only field types and shapes DCM accepts; make keys unique."""
    fields: list[dict] = []
    seen: set[str] = set()
    for item in (raw.get('fields') or [])[:MAX_FIELDS * 2]:
        if not isinstance(item, dict):
            continue
        label = _clean(item.get('label'), 120)
        if not label:
            continue
        key = _key(item.get('key') or label, f'field_{len(fields) + 1}')
        if key in seen:
            continue
        seen.add(key)
        field_type = _clean(item.get('type'), 20).lower()
        if field_type not in FIELD_TYPES:
            field_type = 'textarea'
        options: list[str] = []
        if field_type in ('select', 'multiselect'):
            for option in item.get('options') or []:
                text = _clean(option, 80)
                if text and text not in options:
                    options.append(text)
            if len(options) < 2:
                field_type, options = 'text', []
        fields.append({
            'key': key,
            'label': label,
            'type': field_type,
            'required': bool(item.get('required')),
            'placeholder': _clean(item.get('placeholder'), 160),
            'options': options[:12],
            'auto_fill': '',
        })
        if len(fields) >= MAX_FIELDS:
            break
    if not fields:
        raise AIError('The AI did not return a usable template. Try again or add a short instruction.')
    return {
        'name': _clean(raw.get('name'), 200) or 'Session note (AI draft)',
        'description': _clean(raw.get('description'), 500),
        'fields': fields,
    }


def generate_template_draft(program, instruction: str = '') -> dict:
    targets = [t.name for t in program.targets.all()[:MAX_TARGETS_SENT]]
    lines = [
        f'Program: {_clean(program.name, 200)}',
        f'Category: {program.category}',
    ]
    if program.objective:
        lines.append(f'Objective: {_clean(program.objective, 500)}')
    if targets:
        lines.append('Targets: ' + '; '.join(_clean(t, 160) for t in targets))
    extra = _clean(instruction, MAX_NOTE_CHARS)
    if extra:
        lines.append(f'Extra instruction: {extra}')
    system = SYSTEM_PROMPT.format(types=', '.join(FIELD_TYPES))
    raw = chat_json(system, 'Draft a session note template for this program.\n' + '\n'.join(lines), max_tokens=2500)
    return normalize_template_draft(raw)
