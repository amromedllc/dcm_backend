"""Atomic structured draft reconciliation. Never accepts vendor wording from a client."""
import hashlib
import json

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from ninja.errors import HttpError

from apps.integrations.practice_planner.snapshots import retain_selection
from .models import (
    ClientDiagnosis, PlanActivation, TreatmentPlan, TreatmentPlanDiagnosis, TreatmentPlanProblem,
    TreatmentPlanGoal, TreatmentPlanObjective, TreatmentPlanIntervention, TreatmentPlanSignature,
)

LEVELS = (
    (TreatmentPlanProblem, 'plan', 'goals'),
    (TreatmentPlanGoal, 'problem', 'objectives'),
    (TreatmentPlanObjective, 'goal', 'interventions'),
    (TreatmentPlanIntervention, 'objective', None),
)


def source_output(source):
    return {name: getattr(source, name) for name in (
        'provider', 'planner_id', 'component_id', 'category', 'text', 'edition', 'version', 'language', 'attribution',
    )} | {'retired': source.normalized_payload.get('retired', False)}


def diagnosis_output(diagnosis):
    return {name: getattr(diagnosis, name) for name in (
        'id', 'code_system', 'code_version', 'code', 'display', 'clinical_status',
        'effective_from', 'effective_to', 'supersedes_id', 'lock_version',
    )}


def draft_output(plan):
    def rows(parent, level):
        model, parent_field, children = LEVELS[level]
        result = []
        for row in model.objects.filter(**{parent_field: parent}).select_related('source'):
            item = {'id': row.pk, 'source': source_output(row.source),
                    'clinician_text': row.clinician_text, 'position': row.position}
            if children:
                item[children] = rows(row, level + 1)
            result.append(item)
        return result
    return {name: getattr(plan, name) for name in (
        'id', 'client_id', 'title', 'plan_date', 'date_from', 'date_to', 'status',
        'planning_mode', 'lock_version', 'plan_series_id', 'revision_number',
    )} | {'diagnoses': [{field: getattr(row, field) for field in (
        'diagnosis_id', 'code_system', 'code_version', 'code', 'display',
    )} for row in plan.diagnosis_selections.order_by('id')], 'problems': rows(plan, 0)}


def validate_size(data):
    total = 0
    def count(rows, level=0):
        nonlocal total
        total += len(rows)
        if total > 400:
            raise HttpError(400, 'A draft may contain at most 400 structured rows')
        children = LEVELS[level][2]
        if children:
            for row in rows:
                count(getattr(row, children), level + 1)
    count(data.problems)
    if data.date_from and data.date_to and data.date_from > data.date_to:
        raise HttpError(400, 'date_from cannot be after date_to')
    if len(set(data.diagnosis_ids)) != len(data.diagnosis_ids) or any(pk < 1 for pk in data.diagnosis_ids):
        raise HttpError(400, 'Diagnosis IDs must be distinct positive identifiers')


def reconcile_draft(plan, data, service, actor):
    validate_size(data)
    diagnoses = {d.pk: d for d in ClientDiagnosis.objects.select_for_update().filter(client_id=plan.client_id, pk__in=data.diagnosis_ids)}
    if len(diagnoses) != len(data.diagnosis_ids):
        raise HttpError(400, 'A diagnosis is not available for this client')
    selections = {d.diagnosis_id: d for d in plan.diagnosis_selections.all()}
    for pk, diagnosis in diagnoses.items():
        if pk not in selections:
            if diagnosis.clinical_status != 'active':
                raise HttpError(400, 'Only active diagnoses can be newly selected')
            TreatmentPlanDiagnosis.objects.create(plan=plan, diagnosis=diagnosis, created_by=actor)
    plan.diagnosis_selections.exclude(diagnosis_id__in=data.diagnosis_ids).delete()

    def reconcile(parent, inputs, level):
        model, parent_field, children = LEVELS[level]
        existing = {r.pk: r for r in model.objects.filter(**{parent_field: parent}).select_related('source')}
        retained = set()
        for position, item in enumerate(inputs):
            if item.id is not None:
                if item.id not in existing or item.id in retained:
                    raise HttpError(400, 'Structured row does not belong to this parent or is duplicated')
                row = existing[item.id]
                # Existing retained sources stay frozen, including later-retired content.
                if any(getattr(row.source, field) != getattr(item.source, field) for field in ('component_id', 'edition', 'language')):
                    raise HttpError(400, 'An existing source cannot be replaced; remove the row and select new content')
            else:
                content = service.get_component(item.source.component_id, edition=item.source.edition, language=item.source.language)
                row = model(**{parent_field: parent}, source=retain_selection(content), created_by=actor)
            row.position = position
            row.clinician_text = item.clinician_text
            row.save()
            retained.add(row.pk)
            if children:
                reconcile(row, getattr(item, children), level + 1)
        model.objects.filter(pk__in=set(existing) - retained).delete()
    reconcile(plan, data.problems, 0)


@transaction.atomic
def save_draft(client, data, service, actor, plan_id=None):
    if plan_id is None:
        plan = TreatmentPlan(client=client, planning_mode='structured', created_by=actor)
    else:
        try:
            plan = TreatmentPlan.objects.select_for_update().get(pk=plan_id, client=client, planning_mode='structured')
        except TreatmentPlan.DoesNotExist:
            raise HttpError(404, 'Structured plan not found') from None
        if plan.status != 'draft' or plan.is_signed:
            raise HttpError(409, 'Only unsigned drafts can be edited')
        if plan.lock_version != data.expected_lock_version:
            raise HttpError(409, 'This draft changed. Reload the saved version before trying again; your edits have not been applied.')
        plan.lock_version += 1
    for field in ('title', 'plan_date', 'date_from', 'date_to'):
        setattr(plan, field, getattr(data, field))
    validate_size(data)
    plan.save()
    reconcile_draft(plan, data, service, actor)
    return draft_output(plan)


def structured_content_hash(plan):
    """Fingerprint the complete frozen structured payload, not just plan metadata.

    Includes the diagnosis selections and full problem→goal→objective→intervention
    tree exactly as draft_output renders it, so a signature binds to everything a
    clinician actually reviewed.
    """
    payload = draft_output(plan)
    body = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _get_draft_for_update(client, plan_id):
    try:
        return TreatmentPlan.objects.select_for_update().get(pk=plan_id, client=client, planning_mode='structured')
    except TreatmentPlan.DoesNotExist:
        raise HttpError(404, 'Structured plan not found') from None


def _check_lock(plan, expected_lock_version):
    if plan.lock_version != expected_lock_version:
        raise HttpError(409, 'This plan changed. Reload before trying again.')


@transaction.atomic
def finalize_plan(client, plan_id, data, actor):
    plan = _get_draft_for_update(client, plan_id)
    if plan.status != TreatmentPlan.Status.DRAFT:
        raise HttpError(409, 'Only a draft plan can be finalized')
    _check_lock(plan, data.expected_lock_version)
    if not plan.problems.exists():
        raise HttpError(400, 'A plan needs at least one problem before it can be finalized')
    plan.status = TreatmentPlan.Status.FINALIZED
    plan.finalized_at = timezone.now()
    plan.finalized_by = actor
    plan.lock_version += 1
    plan.save()
    return draft_output(plan)


@transaction.atomic
def sign_plan(client, plan_id, data, actor):
    plan = _get_draft_for_update(client, plan_id)
    if plan.status != TreatmentPlan.Status.FINALIZED:
        raise HttpError(409, 'Finalize the plan before signing it')
    _check_lock(plan, data.expected_lock_version)
    if not data.attested:
        raise HttpError(400, 'Confirm that you have reviewed the plan before signing')
    if plan.signatures.filter(signer_id=actor.id).exists():
        raise HttpError(409, 'You have already signed this plan')
    TreatmentPlanSignature.objects.create(
        plan=plan, signer_id=actor.id, signer_name=actor.full_name, signer_role=actor.role,
        signature_data=data.signature_data.strip()[:200], content_hash=structured_content_hash(plan),
    )
    return draft_output(plan)


def _copy_tree(parent, source_rows, level, actor):
    model, parent_field, children = LEVELS[level]
    for row in source_rows:
        copy = model.objects.create(**{parent_field: parent}, source=row.source,
            clinician_text=row.clinician_text, position=row.position, created_by=actor)
        if children:
            _copy_tree(copy, list(getattr(row, children).all()), level + 1, actor)


@transaction.atomic
def revise_plan(client, plan_id, data, actor):
    plan = _get_draft_for_update(client, plan_id)
    if plan.status not in (TreatmentPlan.Status.FINALIZED, TreatmentPlan.Status.ARCHIVED):
        raise HttpError(409, 'Only a finalized or archived plan can be revised')
    _check_lock(plan, data.expected_lock_version)
    if plan.next_revisions.exists():
        raise HttpError(409, 'A later revision already exists for this plan')
    revision = TreatmentPlan.objects.create(
        client=client, planning_mode='structured', status=TreatmentPlan.Status.DRAFT,
        title=plan.title, plan_date=plan.plan_date, date_from=plan.date_from, date_to=plan.date_to,
        plan_series_id=plan.plan_series_id, revision_number=plan.revision_number + 1,
        previous_revision=plan, created_by=actor,
    )
    for selection in plan.diagnosis_selections.select_related('diagnosis').all():
        TreatmentPlanDiagnosis.objects.create(plan=revision, diagnosis=selection.diagnosis, created_by=actor)
    _copy_tree(revision, list(plan.problems.select_related('source').all()), 0, actor)
    return draft_output(revision)


@transaction.atomic
def record_activation(client, plan_id, data, actor):
    plan = _get_draft_for_update(client, plan_id)
    if plan.status not in (TreatmentPlan.Status.FINALIZED, TreatmentPlan.Status.ARCHIVED):
        raise HttpError(409, 'Only a finalized or archived plan can be activated')
    _check_lock(plan, data.expected_lock_version)
    if data.effective_to and data.effective_to < data.effective_from:
        raise HttpError(400, 'effective_to cannot be before effective_from')
    activation = PlanActivation.objects.create(
        plan=plan, effective_from=data.effective_from, effective_to=data.effective_to, activated_by=actor,
    )
    return {field: getattr(activation, field) for field in ('id', 'effective_from', 'effective_to', 'activated_at')}


def activation_history(plan):
    return [{field: getattr(row, field) for field in ('id', 'effective_from', 'effective_to', 'activated_at')}
            for row in plan.activations.order_by('effective_from', 'id')]


def resolve_active_plan_for_client(client, as_of_date):
    """The structured plan revision in effect for this client on a given date.

    Picks the most recently recorded activation whose effective range covers
    the date. Stage 5 (progress-note autofill) uses this to find what a
    clinician should be drawing from; it never falls back to an unactivated
    draft or finalized-but-not-yet-active plan.
    """
    activation = PlanActivation.objects.filter(
        plan__client=client, plan__planning_mode='structured', effective_from__lte=as_of_date,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=as_of_date)).select_related('plan').order_by('-activated_at', '-id').first()
    return activation.plan if activation else None
