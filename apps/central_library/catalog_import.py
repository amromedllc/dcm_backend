"""Map the Progressly workbook's data to the current Central Library schema.

Workbook prose is source data, never executable instructions. Unsupported
recording modes remain instruction-only drafts rather than collecting a
different metric. Instruction fields contain only teaching content. The accompanying source
JSON retains every workbook cell, including metadata and unsupported settings.
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


def teaching_text(row, *, program=False):
    """Only explicit teaching fields belong in instructions; never dump a row."""
    fields = [('instructions', ''), ('teaching_instructions', '')]
    if program:
        fields += [
            ('instruction_sd', 'Instruction / SD'),
            ('prompting_procedure', 'Prompting procedure'),
            ('error_correction', 'Error correction'),
            ('reinforcement_plan', 'Reinforcement plan'),
        ]
    parts = []
    for key, label in fields:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            parts.append(f'{label}: {text}' if label else text)
    return '\n\n'.join(parts)


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
    if not isinstance(document, dict) or document.get('format') not in {'progressly-workbook-v1', 'progressly-workbook-v2'}:
        raise ValueError('Expected a progressly workbook export')
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
                'instructions': teaching_text(row, program=True),
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
                'teaching_instructions': teaching_text(row),
                'prompting_levels': levels,
                'display_order': len(program['targets']) * 10, 'is_visible_to_staff': True,
            },
        })
    for program in result:
        if not program['targets']:
            raise ValueError(f'{program["source_id"]}: program has no targets')
    if document['format'] == 'progressly-workbook-v2':
        _apply_settings_workbook(document, result, programs, targets)
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
        'Imported as instructions only; the source workbook retains the original options.'
    )



def _json_object(value, context):
    import json
    try:
        data = json.loads(value or '{}')
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{context}: invalid JSON') from exc
    if not isinstance(data, dict):
        raise ValueError(f'{context}: JSON must be an object')
    return data


def build_settings(document):
    """Validate workbook references without looking up database IDs."""
    from ninja.errors import HttpError
    from .settings_service import normalize_configuration
    if document.get('format') != 'progressly-workbook-v2':
        return {}
    sheets = document['sheets']
    for name in ('Central Settings', 'Program Setting Links', 'Target Setting Links'):
        if name not in sheets or not isinstance(sheets[name], list):
            raise ValueError(f'Missing or invalid sheet: {name}')
    rows = sheets['Central Settings']
    indexed = _index(rows, 'setting_id') if rows else {}
    settings, names, keys = {}, set(), set()
    for identity, row in indexed.items():
        name, kind = _required(row, 'name'), _required(row, 'kind')
        status = row.get('review_status')
        if status not in {'ready_for_import', 'needs_clinician_review'}:
            raise ValueError(f'{identity}: unknown review_status')
        if (kind, name) in names:
            raise ValueError(f'{identity}: duplicate setting kind/name')
        names.add((kind, name))
        try:
            configuration = normalize_configuration(kind, name, _json_object(row.get('configuration_json'), identity))
        except HttpError as exc:
            raise ValueError(f'{identity}: {exc}') from exc
        if kind == 'target_status':
            if configuration['key'] in keys:
                raise ValueError(f'{identity}: duplicate target-status key')
            keys.add(configuration['key'])
        settings[identity] = dict(source_id=identity, kind=kind, name=name, configuration=configuration,
            ready=status == 'ready_for_import', dependencies=split_values(row.get('dependency_setting_ids')))
    for identity, setting in settings.items():
        deps = setting['dependencies']
        if len(deps) != len(set(deps)):
            raise ValueError(f'{identity}: duplicate dependencies')
        if setting['kind'] != 'workflow' and deps:
            raise ValueError(f'{identity}: only workflows accept dependencies')
        for ref in deps:
            if ref not in settings or settings[ref]['kind'] != 'target_status':
                raise ValueError(f'{identity}: dependency {ref} is not a defined target status')
            if setting['ready'] and not settings[ref]['ready']:
                raise ValueError(f'{identity}: dependency {ref} needs clinician review')
        if setting['kind'] == 'workflow':
            phases = setting['configuration']['phases']
            phase_keys = [phase['phase'] for phase in phases]
            if len(set(phase_keys)) != len(phase_keys):
                raise ValueError(f'{identity}: duplicate workflow phases')
            keys = {settings[ref]['configuration']['key'] for ref in deps}
            for phase in phases:
                for key in ('phase', 'on_success', 'on_regression', 'on_failure_status'):
                    if phase.get(key) and phase[key] not in keys:
                        raise ValueError(f'{identity}: undefined workflow status {phase[key]}')
                criteria = phase.get('criteria') or {}
                if not isinstance(criteria, dict):
                    raise ValueError(f'{identity}: criteria must be an object')
                for key in ('minimum_trials', 'consecutive_sessions'):
                    if key in criteria and (not isinstance(criteria[key], int) or criteria[key] < 1):
                        raise ValueError(f'{identity}: {key} must be a positive integer')
    return settings


def _apply_settings_workbook(document, catalog, program_rows, target_rows):
    from pydantic import ValidationError
    from types import SimpleNamespace
    from apps.programs.schemas import CentralProgramConfiguration, CentralTargetConfiguration
    from apps.programs.api import _workflow_matches_measurement
    from shared.html_sanitize import sanitize_kb_html
    from .settings_service import SINGULAR
    settings = build_settings(document)
    programs = {p['source_id']: p for p in catalog}
    targets = {t['source_id']: t for p in catalog for t in p['targets']}
    for name, entities, id_field, allowed in (
        ('Program Setting Links', programs, 'program_id', set(['prompting', 'workflow', 'treatment_area', 'tag', 'target_status', 'data_field'])),
        ('Target Setting Links', targets, 'target_id', {'prompting', 'workflow', 'target_status'}),
    ):
        seen = set()
        for row in document['sheets'][name]:
            owner, ref = _required(row, id_field), _required(row, 'setting_id')
            if owner not in entities or ref not in settings:
                raise ValueError(f'{name}: unknown reference {owner} / {ref}')
            if (owner, ref) in seen:
                raise ValueError(f'{name}: duplicate link {owner} / {ref}')
            seen.add((owner, ref))
            setting = settings[ref]
            if not setting['ready'] or setting['kind'] not in allowed:
                raise ValueError(f'{owner}: setting {ref} is not ready or has the wrong kind')
            refs = entities[owner].setdefault('setting_refs', [])
            if setting['kind'] in SINGULAR and any(settings[x]['kind'] == setting['kind'] for x in refs):
                raise ValueError(f'{owner}: multiple {setting["kind"]} settings')
            refs.append(ref)
    for entities, rows, schema in ((programs, program_rows, CentralProgramConfiguration), (targets, target_rows, CentralTargetConfiguration)):
        for identity, entity in entities.items():
            try:
                config = schema(**_json_object(rows[identity].get('configuration_json'), identity)).model_dump()
            except ValidationError as exc:
                raise ValueError(f'{identity}: invalid configuration: {exc}') from exc
            for key in ('instructions_html', 'professional_instructions_html'):
                if key in config:
                    config[key] = sanitize_kb_html(config[key])
            entity['fields']['configuration'] = config
            entity.setdefault('setting_refs', [])
    for program in catalog:
        linked = {settings[ref]['kind']: settings[ref] for ref in program['setting_refs']}
        if 'treatment_area' in linked:
            program['fields']['treatment_area'] = linked['treatment_area']['name']
        # Workbook links are authoritative; do not recreate the v1 synthetic tags.
        program['fields']['tags'] = [settings[ref]['name'] for ref in program['setting_refs'] if settings[ref]['kind'] == 'tag']
        for ref in program['fields']['configuration'].get('custom_field_values', {}):
            if ref not in program['setting_refs'] or settings[ref]['kind'] != 'data_field':
                raise ValueError(f'{program["source_id"]}: custom field {ref} is not linked')
        for target in program['targets']:
            own = {settings[ref]['kind']: settings[ref] for ref in target['setting_refs']}
            if 'prompting' in own or 'prompting' in linked:
                # Inheritance must not be shadowed by a second inline template
                # reconstructed from legacy workbook prompt labels.
                target['fields']['prompting_levels'] = []
            workflow = own.get('workflow', linked.get('workflow'))
            if workflow and target['fields']['measurement_type'] != 'instructions':
                if not _workflow_matches_measurement(SimpleNamespace(phases=workflow['configuration']['phases']), target['fields']['measurement']):
                    raise ValueError(f'{target["source_id"]}: incompatible workflow measurement')
            if target['fields']['configuration']['mastery_mode'] == 'automatic' and not workflow:
                raise ValueError(f'{target["source_id"]}: automatic mastery requires a workflow')
