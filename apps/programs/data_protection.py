"""Keep live/offline session definitions and recorded measurements interpretable."""
from ninja.errors import HttpError

from apps.sessions.models import BehaviorEvent, SessionRun, TrialEvent


DEFINITION_FIELDS = (
    'measurement_type', 'measurement', 'timer_type', 'prompting_template_id',
    'interval_seconds', 'interval_sync_with_session',
    'sub_item_progression', 'default_sub_measurement_type',
    'default_sub_measurement', 'default_sub_prompting_template_id',
)
MESSAGE = (
    'This target has recorded data or is included in a saved session. '
    'Its measurement, scoring, and step structure cannot be changed. '
    'Create a new target for a different definition; names, instructions, status, '
    'and workflow settings remain editable.'
)


def protected_target_ids(target_ids):
    ids = set(target_ids)
    if not ids:
        return set()
    protected = set()
    for model in (TrialEvent, BehaviorEvent):
        protected.update(model.objects.filter(target_id__in=ids).values_list('target_id', flat=True))
    if protected == ids:
        return protected
    # Includes unsynced offline sessions. Limit reads to the affected clients.
    from apps.clients.id_aliases import client_identifier_aliases
    from .models import Target
    clients = Target.objects.filter(id__in=ids).values_list('program__external_client_id', flat=True).distinct()
    aliases = set()
    for client_id in clients:
        if client_id is not None:
            aliases.update(client_identifier_aliases(client_id))
    sessions = SessionRun.objects.filter(external_client_id__in=aliases).exclude(program_snapshot={})
    for snapshot in sessions.values_list('program_snapshot', flat=True).iterator():
        for program in (snapshot or {}).get('programs', []):
            protected.update(t.get('id') for t in program.get('targets', []) if t.get('id') in ids)
        if protected == ids:
            break
    return protected


def guard_target_changes(target, updates, protected=None):
    changed = any(field in updates and updates[field] != getattr(target, field) for field in DEFINITION_FIELDS)
    if changed and (target.id in protected if protected is not None else protected_target_ids([target.id])):
        raise HttpError(409, MESSAGE)


def scoring_signature(levels):
    return [(level.get('label'), level.get('score'), level.get('is_success')) for level in levels]


def template_has_history(template_id):
    from django.db.models import Q
    from .models import Target
    ids = Target.objects.filter(
        Q(prompting_template_id=template_id)
        | Q(program__prompting_template_id=template_id)
        | Q(default_sub_prompting_template_id=template_id)
        | Q(child_items__prompting_template_id=template_id)
    ).values_list('id', flat=True).distinct()
    if protected_target_ids(ids):
        return True
    # Also retain templates referenced by historical snapshots after reassignment.
    for snapshot in SessionRun.objects.exclude(program_snapshot={}).values_list('program_snapshot', flat=True).iterator():
        for program in (snapshot or {}).get('programs', []):
            for target in program.get('targets', []):
                if (target.get('prompting_template') or {}).get('id') == template_id:
                    return True
                if any(child.get('prompting_template_id') == template_id for child in target.get('sub_items', [])):
                    return True
    return False
