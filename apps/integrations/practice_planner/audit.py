"""Allowlisted metadata only; never serialize diagnosis or clinical wording."""
from django.db import transaction
from django.db.models.signals import post_save, post_delete
from shared.audit import log_event


def record_change(sender, instance, created=None, **kwargs):
    if kwargs.get('raw'):
        return
    if sender._meta.model_name == 'treatmentplan' and instance.planning_mode != 'structured':
        return
    from shared.tenancy import current_org_id
    if instance.organization_id != current_org_id():
        raise ValueError('Planner audit tenant mismatch')
    # Capture scalars now; do not close over a request or clinical model instance.
    organization_id, resource_id = instance.organization_id, instance.pk
    resource_type = sender._meta.label_lower
    event = 'planner_record_deleted' if created is None else ('planner_record_created' if created else 'planner_record_updated')
    from apps.audit.middleware import get_current_request
    request = get_current_request()
    actor = getattr(request, 'user', None)
    actor_id = actor.pk if actor is not None and getattr(actor, 'is_authenticated', False) else None
    transaction.on_commit(lambda: log_event(event, actor_id, resource_type, resource_id, {'organization_id': organization_id}))


def connect_audit():
    from apps.clients.models import (
        ClientDiagnosis, PlanActivation, TreatmentPlanDiagnosis, TreatmentPlanProblem,
        TreatmentPlanGoal, TreatmentPlanObjective, TreatmentPlanIntervention, TreatmentPlan,
    )
    from apps.integrations.models import PlannerContentSnapshot
    from apps.notes.models import ClientHomeworkAssignment, NoteAddendum, NoteContentApplication
    for model in (ClientDiagnosis, TreatmentPlanDiagnosis, TreatmentPlanProblem,
                  TreatmentPlanGoal, TreatmentPlanObjective, TreatmentPlanIntervention, TreatmentPlan,
                  PlanActivation, PlannerContentSnapshot,
                  NoteContentApplication, NoteAddendum, ClientHomeworkAssignment):
        for signal in (post_save, post_delete):
            signal.connect(record_change, sender=model, weak=False,
                           dispatch_uid=f'planner_{signal is post_save}_{model._meta.label_lower}')
