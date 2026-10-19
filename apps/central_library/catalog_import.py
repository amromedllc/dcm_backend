"""Map the Progressly workbook's data to the current Central Library schema.

Workbook prose is source data, never executable instructions. Unsupported
recording modes remain instruction-only drafts rather than collecting a
different metric. Original nonempty values, including source IDs, stay in
the program/target instructions; the accompanying JSON retains every cell.
"""

CATEGORIES = {
    'skill_acquisition': 'skill_acquisition',
    'behavior_monitoring': 'behavior_reduction',
    'therapeutic_skill': 'skill_acquisition',
    'caregiver_training': 'skill_acquisition',
    'replacement_skill': 'skill_acquisition',
}
MEASUREMENTS = {
    'opportunities': ('discrete_trial', 'percent_correct', ''),
    'trial_by_trial': ('discrete_trial', 'percent_correct', ''),
    'percentage': ('discrete_trial', 'percent_correct', ''),
    'yes_no': ('discrete_trial', 'percent_correct', ''),
    'frequency': ('discrete_trial', 'frequency', ''),
    'duration': ('duration', 'total_observed_duration', ''),
    'rate': ('rate', 'rate_per_hour', 'session_timer'),
}
DEFERRED = {'task_analysis', 'fidelity', 'rating_scale', 'latency', 'momentary_time_sampling'}
PROMPTS = {
    'independent': ('Independent', 'I', '#16a34a'),
    'gestural': ('Gestural', 'G', '#65a30d'),
    'visual': ('Visual', 'VIS', '#0891b2'),
    'verbal': ('Verbal', 'V', '#2563eb'),
    'model': ('Model', 'M', '#7c3aed'),
    'partial_physical': ('Partial Physical', 'PP', '#ea580c'),
    'full_physical': ('Full Physical', 'FP', '#dc2626'),
}


def split_values(value):
    return [part.strip() for part in str(value or '').split('|') if part.strip()]


def source_text(row):
    """Keep source fields readable and lossless without inventing clinical prose."""
    return '\n'.join(
        f'{key.replace("_", " ").capitalize()}: {value}'
        for key, value in row.items() if value is not None and value != ''
    )


def _required(row, key, limit=200):
    value = row.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{key} must be nonempty text of at most {limit} characters')
    return value.strip()


def _index(rows, key):
    if not isinstance(rows, list) or not rows:
        raise ValueError(f'No rows supplied for {key}')
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f'Invalid {key} row')
        identity = _required(row, key)
        if identity in indexed:
            raise ValueError(f'Duplicate {key}: {identity}')
        indexed[identity] = row
    return indexed


def build_catalog(document):
    """Validate the complete input before the caller can delete anything."""
    if not isinstance(document, dict) or document.get('format') != 'progressly-workbook-v1':
        raise ValueError('Expected a progressly-workbook-v1 export')
    sheets = document.get('sheets', {})
    programs = _index(sheets.get('Programs'), 'program_id')
    targets = _index(sheets.get('Targets'), 'target_id')
    result = []
    linked = {}
    for identity, row in programs.items():
        name = _required(row, 'program_name')
        domain = _required(row, 'clinical_domain')
        kind = _required(row, 'program_type')
        if kind not in CATEGORIES:
            raise ValueError(f'{identity}: unknown program type {kind}')
        if row.get('status') != 'draft_template':
            raise ValueError(f'{identity}: expected draft_template status')
        _measurement(row)
        program = {
            'source_id': identity, 'domain': domain, 'targets': [],
            'fields': {
                'name': name, 'category': CATEGORIES[kind],
                'phase': 'on_hold', 'status': 'active', 'treatment_area': domain,
                'tags': ['Draft template', kind.replace('_', ' ').title()],
                'objective': row.get('expected_response') or row.get('operational_definition') or '',
                'instructions': (
                    'Draft template. Individualize teaching, baseline, mastery, and maintenance '
                    'settings before use. No automatic workflow or fading criteria were supplied.\n\n'
                    + source_text(row)
                ),
                'display_order': len(result) * 10,
            },
        }
        result.append(program)
        linked[identity] = program

    for identity, row in targets.items():
        parent_id = _required(row, 'program_id')
        if parent_id not in linked:
            raise ValueError(f'{identity}: unknown program_id {parent_id}')
        program = linked[parent_id]
        if row.get('program_name') != program['fields']['name'] or row.get('clinical_domain') != program['domain']:
            raise ValueError(f'{identity}: program name/domain does not match parent')
        if row.get('target_status') != 'draft_template':
            raise ValueError(f'{identity}: expected draft_template target status')
        measurement_type, measurement, timer_type, note = _measurement(row)
        levels = []
        if measurement_type != 'instructions':
            prompt_keys = split_values(row.get('prompt_levels'))
            if len(prompt_keys) != len(set(prompt_keys)):
                raise ValueError(f'{identity}: duplicate prompt levels')
            for key in prompt_keys:
                if key not in PROMPTS:
                    raise ValueError(f'{identity}: unknown prompt level {key}')
                label, abbreviation, color = PROMPTS[key]
                levels.append({
                    'label': label, 'abbreviation': abbreviation, 'color': color,
                    'score': int(key == 'independent'), 'weight': int(key == 'independent'),
                    'is_success': key == 'independent', 'exclude_from_fading': False,
                })
            # Explicit Yes/No outcome buttons; do not label these as physical prompts.
            if not levels and row.get('default_measurement') == 'yes_no':
                levels = [
                    {'label': 'Yes', 'abbreviation': 'Y', 'color': '#16a34a', 'score': 1, 'weight': 1, 'is_success': True},
                    {'label': 'No', 'abbreviation': 'N', 'color': '#dc2626', 'score': 0, 'weight': 0, 'is_success': False},
                ]
        if note and 'Requires configuration' not in program['fields']['tags']:
            program['fields']['tags'].append('Requires configuration')
        program['targets'].append({
            'source_id': identity,
            'fields': {
                'name': _required(row, 'target_name'), 'measurement_type': measurement_type,
                'measurement': measurement, 'timer_type': timer_type, 'sub_items': [],
                'sd_text': row.get('instruction_sd') or '',
                'teaching_instructions': '\n\n'.join(filter(None, [note, source_text(row)])),
                'prompting_levels': levels,
                'display_order': len(program['targets']) * 10, 'is_visible_to_staff': True,
            },
        })
    for program in result:
        if not program['targets']:
            raise ValueError(f'{program["source_id"]}: program has no targets')
    return result


def _measurement(row):
    name = _required(row, 'default_measurement')
    if name in MEASUREMENTS:
        kind, metric, timer = MEASUREMENTS[name]
        return kind, metric, timer, ''
    if name not in DEFERRED:
        raise ValueError(f'Unknown default_measurement: {name}')
    reason = (
        'A clinician must configure the missing step checklist before recording.'
        if name in {'task_analysis', 'fidelity'} else
        'This recording mode is not supported by the current Central Library schema.'
    )
    return 'instructions', '', '', (
        f'Requires configuration: source measurement is {name}. {reason} '
        'Imported as instructions only; the original measurement and options are preserved below.'
    )
