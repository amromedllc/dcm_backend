"""Central settings validation and independent facility copies."""
from copy import deepcopy
from types import SimpleNamespace

from django.db import transaction
from ninja.errors import HttpError  
from pydantic import ValidationError

from shared.tenancy import current_org_id
from .models import CentralProgramSetting, CentralSettingCopy


SINGULAR = {'prompting', 'workflow', 'treatment_area', 'target_status'}


def normalize_configuration(kind, name, configuration):
    from apps.programs.schemas import (
        PromptingTemplateCreateRequest, WorkflowTemplateCreateRequest,
        TreatmentAreaRequest, ProgramTagRequest, TargetStatusRequest, ProgramDataFieldRequest,
    )
    schemas = dict(prompting=PromptingTemplateCreateRequest, workflow=WorkflowTemplateCreateRequest,
                   treatment_area=TreatmentAreaRequest, tag=ProgramTagRequest,
                   target_status=TargetStatusRequest, data_field=ProgramDataFieldRequest)
    if kind not in schemas:
        raise HttpError(400, 'Unknown central setting kind')
    schema = schemas[kind]
    payload = deepcopy(configuration)
    forbidden = {'id', 'organization', 'organization_id', 'created_by', 'created_by_id',
                 'program_template_ids', 'is_org_default', 'is_default'}
    if forbidden.intersection(payload):
        raise HttpError(400, 'Central settings cannot assign facility IDs or facility defaults')
    allowed = set(schema.model_fields) - {'name', 'label'}
    if set(payload) - allowed:
        raise HttpError(400, f'Unknown configuration fields: {", ".join(sorted(set(payload) - allowed))}')
    try:
        data = schema(**({'label': name} if kind == 'target_status' else {'name': name}), **payload).model_dump()
    except ValidationError as exc:
        raise HttpError(400, str(exc)) from exc
    data.pop('name', None)
    data.pop('label', None)
    for field in ('is_org_default', 'is_default', 'program_template_ids'):
        data.pop(field, None)
    if kind == 'tag' and len(name) > 100:
        raise HttpError(400, 'Tag names cannot exceed 100 characters')
    if kind == 'target_status' and len(name) > 50:
        raise HttpError(400, 'Status labels cannot exceed 50 characters')
    if kind == 'prompting':
        if data['outcome_measurement'] == 'rating_scale':
            data.update(fading_hint_mode='none', fading_hint_settings={})
        elif sum(bool(level['is_success']) for level in data['levels']) != 1:
            raise HttpError(400, 'Choose exactly one successful prompt outcome')
        labels = [level['label'] for level in data['levels']]
        if len(labels) != len(set(labels)):
            raise HttpError(400, 'Prompt level labels must be unique')
        hints = data['fading_hint_settings']
        for key in ('decrease_match_pct', 'increase_match_pct'):
            if key in hints and (not isinstance(hints[key], (int, float)) or not 0 <= hints[key] <= 100):
                raise HttpError(400, f'{key} must be between 0 and 100')
        for key in ('decrease_across', 'increase_across', 'probe_threshold'):
            if key in hints and (not isinstance(hints[key], int) or hints[key] < 1):
                raise HttpError(400, f'{key} must be a positive integer')
    if kind == 'data_field':
        # A linked central field applies to imported templates, never every
        # unrelated client program in the destination facility.
        data.update(enable_on_all_programs=False, enable_on_program_templates=True)
    return data


def validate_setting_links(ids, *, target=False):
    if len(ids) != len(set(ids)):
        raise HttpError(400, 'Duplicate central settings')
    settings = list(CentralProgramSetting.objects.filter(id__in=ids, is_active=True))
    if len(settings) != len(ids):
        raise HttpError(400, 'Unknown or inactive central setting')
    counts = {}
    for setting in settings:
        counts[setting.kind] = counts.get(setting.kind, 0) + 1
        if target and setting.kind not in {'prompting', 'workflow', 'target_status'}:
            raise HttpError(400, 'Targets accept prompting, workflow, and starting status settings')
    if any(counts.get(kind, 0) > 1 for kind in SINGULAR):
        raise HttpError(400, 'Choose only one setting of each singular kind')
    return settings


def validate_dependencies(kind, configuration, ids):
    dependencies = list(CentralProgramSetting.objects.filter(id__in=ids, is_active=True))
    if len(dependencies) != len(set(ids)) or any(s.kind != 'target_status' for s in dependencies):
        raise HttpError(400, 'Workflow dependencies must be active central target statuses')
    if kind != 'workflow' and ids:
        raise HttpError(400, 'Only workflows can have status dependencies')
    if kind == 'workflow':
        phases = configuration['phases']
        phase_keys = [phase['phase'] for phase in phases]
        if len(phase_keys) != len(set(phase_keys)):
            raise HttpError(400, 'Workflow phase keys must be unique')
        keys = {setting.configuration['key'] for setting in dependencies}
        referenced = set()
        for phase in phases:
            criteria = phase.get('criteria') or {}
            if not isinstance(criteria, dict):
                raise HttpError(400, 'Workflow criteria must be an object')
            for key in ('minimum_trials', 'consecutive_sessions', 'regression_consecutive_sessions'):
                if key in criteria and (not isinstance(criteria[key], int) or criteria[key] < 1):
                    raise HttpError(400, f'{key} must be a positive integer')
            if 'threshold_pct' in criteria and (
                not isinstance(criteria['threshold_pct'], (int, float)) or criteria['threshold_pct'] < 0
            ):
                raise HttpError(400, 'Workflow threshold must be a nonnegative number')
            for field in ('phase', 'on_success', 'on_regression', 'on_failure_status'):
                if phase.get(field):
                    referenced.add(phase[field])
        if referenced - keys:
            raise HttpError(400, 'Link central target statuses for: ' + ', '.join(sorted(referenced - keys)))
    return dependencies


def _local_model(kind):
    from apps.programs.models import PromptingTemplate, WorkflowTemplate, TreatmentArea, ProgramTag, TargetStatus, ProgramDataField
    return dict(prompting=PromptingTemplate, workflow=WorkflowTemplate, treatment_area=TreatmentArea,
                tag=ProgramTag, target_status=TargetStatus, data_field=ProgramDataField)[kind]


@transaction.atomic
def pull_setting(setting, user):
    """Serialize reuse across program imports and never overwrite local edits."""
    from apps.programs.api import _settings_qs
    setting = CentralProgramSetting.objects.select_for_update().get(pk=setting.pk)
    if not setting.is_active:
        raise HttpError(400, f'Central setting "{setting.name}" is inactive')
    dependencies = list(setting.dependencies.order_by('id'))
    validate_dependencies(setting.kind, setting.configuration, [item.pk for item in dependencies])
    for dependency in dependencies:
        pull_setting(dependency, user)
    org_id = current_org_id()
    practice_key = f'tpms:{user.external_admin_id}' if user.external_admin_id is not None else 'native'
    model = _local_model(setting.kind)
    request = SimpleNamespace(user=user)
    visible = _settings_qs(model, request, include_org_defaults=setting.kind == 'target_status')
    copy = CentralSettingCopy.objects.filter(setting=setting, organization_id=org_id, practice_key=practice_key).first()
    if copy:
        existing = visible.filter(pk=copy.local_id).first()
        if existing is not None:
            return existing
    values = deepcopy(setting.configuration)
    values['label' if setting.kind == 'target_status' else 'name'] = setting.name
    if setting.kind == 'target_status':
        existing = visible.filter(key=values['key']).first()
        if existing is None and model.objects.filter(key=values['key']).exists():
            raise HttpError(409, f'Status key "{values["key"]}" belongs to another facility in this organization')
    else:
        existing = visible.filter(name=setting.name).first()
        if existing and not all(getattr(existing, key) == value for key, value in values.items()):
            existing = None
        if existing is None:
            base = setting.name
            counter = 0
            while model.objects.filter(name=values['name']).exists():
                counter += 1
                suffix = f' (Central {setting.pk}' + (f'-{counter}' if counter > 1 else '') + ')'
                limit = 100 if setting.kind == 'tag' else 200
                values['name'] = base[:limit - len(suffix)] + suffix
    if existing is None:
        existing = model.objects.create(created_by=user, **values)
    CentralSettingCopy.objects.update_or_create(
        setting=setting, organization_id=org_id, practice_key=practice_key,
        defaults={'local_id': existing.pk},
    )
    return existing


def pull_links(source, user):
    linked = {}
    for setting in source.settings.order_by('id'):
        local = pull_setting(setting, user)
        linked.setdefault(setting.kind, []).append((setting.pk, local))
    return linked


def legacy_setting(kind, name, **configuration):
    """Turn legacy text/inline levels into centrally linked reusable settings."""
    configuration = normalize_configuration(kind, name, configuration)
    setting, _ = CentralProgramSetting.objects.get_or_create(kind=kind, name=name, defaults={'configuration': configuration})
    return setting


def sync_legacy_links(source):
    kinds = set(source.settings.values_list('kind', flat=True))
    if hasattr(source, 'treatment_area'):
        if source.treatment_area and 'treatment_area' not in kinds:
            source.settings.add(legacy_setting('treatment_area', source.treatment_area))
        linked_tags = set(source.settings.filter(kind='tag').values_list('name', flat=True))
        for tag in source.tags:
            if tag not in linked_tags:
                source.settings.add(legacy_setting('tag', tag))
    if source.prompting_levels and 'prompting' not in kinds:
        configuration = normalize_configuration('prompting', 'Imported prompting', {'levels': source.prompting_levels})
        match = CentralProgramSetting.objects.filter(kind='prompting', configuration=configuration).first()
        if match is None:
            # Include model and pk to avoid collisions with a differently
            # configured source that happens to have the same display name.
            match = legacy_setting('prompting', f'{source.name[:140]} ({source._meta.model_name} {source.pk})',
                                   levels=source.prompting_levels)
        source.settings.add(match)


def apply_program_settings(dest, source, user):
    """Called only on first creation; later partial imports keep local choices."""
    sync_legacy_links(source)
    linked = pull_links(source, user)
    for kind, field in [('prompting', 'prompting_template'), ('workflow', 'workflow_template')]:
        if linked.get(kind):
            setattr(dest, field, linked[kind][0][1])
    if linked.get('treatment_area'):
        dest.treatment_area = linked['treatment_area'][0][1].name
    if linked.get('tag'):
        dest.tags = [item.name for _, item in linked['tag']]
    configuration = deepcopy(source.configuration)
    values = configuration.pop('custom_field_values', {})
    dest.custom_field_values = {}
    for key, field in linked.get('data_field', []):
        if str(key) in values:
            dest.custom_field_values[str(field.pk)] = values[str(key)]
    for field, value in configuration.items():
        setattr(dest, field, value)
    dest.save()
    for _, field in linked.get('data_field', []):
        if dest.pk not in field.program_template_ids:
            field.program_template_ids = [*field.program_template_ids, dest.pk]
            field.save(update_fields=['program_template_ids'])
    return linked
