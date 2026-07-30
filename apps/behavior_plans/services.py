from django.db import transaction
from django.utils import timezone
from ninja.errors import HttpError

from shared.audit import log_bip_status_change
from .models import BehaviorInterventionPlan
from .schemas import CONTENT_FIELD_NAMES

REQUIRED_FOR_ACTIVATION = (
    'target_behavior_name',
    'target_behavior_definition',
    'antecedent_strategies',
    'replacement_behaviors',
    'response_procedures',
)

_FIELD_LABELS = {
    'target_behavior_name': 'Target Behavior',
    'target_behavior_definition': 'Operational Definition',
    'antecedent_strategies': 'Antecedent / Prevention Strategies',
    'replacement_behaviors': 'Replacement Behavior(s)',
    'response_procedures': 'Staff Response Procedure',
}


def _full_name(user) -> str:
    if user is None:
        return ''
    name = f'{user.first_name} {user.last_name}'.strip()
    return name or user.email


def _assert_editable(plan: BehaviorInterventionPlan) -> None:
    if not plan.is_editable:
        raise HttpError(409, f'Plan is {plan.status} and cannot be modified')


def _validate_required_content(plan: BehaviorInterventionPlan) -> None:
    missing = [
        _FIELD_LABELS[field]
        for field in REQUIRED_FOR_ACTIVATION
        if not getattr(plan, field)
    ]
    if missing:
        raise HttpError(422, f'Required fields are missing: {", ".join(missing)}')


def create_revision(
    source: BehaviorInterventionPlan, user, title: str | None = None,
) -> BehaviorInterventionPlan:
    """Clone `source`'s content into a new DRAFT, chained via previous_version."""
    with transaction.atomic():
        next_version_number = (
            BehaviorInterventionPlan.objects
            .select_for_update()
            .filter(program_id=source.program_id)
            .order_by('-version_number')
            .values_list('version_number', flat=True)
            .first() or 0
        ) + 1

        revision = BehaviorInterventionPlan.objects.create(
            program=source.program,
            version_number=next_version_number,
            previous_version=source,
            status=BehaviorInterventionPlan.Status.DRAFT,
            author=user,
            author_name=_full_name(user),
            created_by=user,
            title=title if title is not None else source.title,
            **{field: getattr(source, field) for field in CONTENT_FIELD_NAMES if field != 'title'},
        )
    log_bip_status_change(
        user.id, revision.id, None, revision.status,
        client_id=revision.client_id, version_number=revision.version_number,
    )
    return revision


def submit_bip_version(plan: BehaviorInterventionPlan, user) -> None:
    """Draft|Rejected -> Submitted."""
    if plan.status not in (BehaviorInterventionPlan.Status.DRAFT, BehaviorInterventionPlan.Status.REJECTED):
        raise HttpError(409, f'Plan must be a draft or rejected to submit (current: {plan.status})')
    old_status = plan.status
    plan.status = BehaviorInterventionPlan.Status.SUBMITTED
    plan.submitted_at = timezone.now()
    plan.rejection_reason = ''
    plan.rejected_by_id = None
    plan.rejected_at = None
    plan.save(update_fields=['status', 'submitted_at', 'rejection_reason', 'rejected_by_id', 'rejected_at'])
    log_bip_status_change(user.id, plan.id, old_status, plan.status,
                           client_id=plan.client_id, version_number=plan.version_number)


def reject_bip_version(plan: BehaviorInterventionPlan, reviewer, reason: str) -> None:
    """Submitted -> Rejected. A reason is mandatory."""
    if plan.status != BehaviorInterventionPlan.Status.SUBMITTED:
        raise HttpError(409, f'Plan must be submitted before rejection (current: {plan.status})')
    if not reason.strip():
        raise HttpError(400, 'A rejection reason is required')
    old_status = plan.status
    plan.status = BehaviorInterventionPlan.Status.REJECTED
    plan.rejected_by_id = reviewer.id
    plan.rejected_at = timezone.now()
    plan.rejection_reason = reason
    plan.save(update_fields=['status', 'rejected_by_id', 'rejected_at', 'rejection_reason'])
    log_bip_status_change(reviewer.id, plan.id, old_status, plan.status,
                           client_id=plan.client_id, version_number=plan.version_number)


def activate_bip_version(
    plan: BehaviorInterventionPlan, actor, effective_date=None, review_due_date=None,
) -> None:
    """Draft|Submitted -> Active. Auto-archives any other active plan for the
    same program first, so the partial unique constraint is never tripped.
    Scoped per-program (not per-client) — a client's other behavior-reduction
    programs keep their own independently-active plans untouched."""
    if plan.status not in (BehaviorInterventionPlan.Status.DRAFT, BehaviorInterventionPlan.Status.SUBMITTED):
        raise HttpError(409, f'Plan must be a draft or submitted to activate (current: {plan.status})')
    _validate_required_content(plan)

    with transaction.atomic():
        currently_active = (
            BehaviorInterventionPlan.objects
            .select_for_update()
            .filter(program_id=plan.program_id, status=BehaviorInterventionPlan.Status.ACTIVE)
            .exclude(id=plan.id)
        )
        for other in currently_active:
            old = other.status
            other.status = BehaviorInterventionPlan.Status.ARCHIVED
            other.archived_by_id = actor.id
            other.archived_at = timezone.now()
            other.archive_reason = f'Superseded by version {plan.version_number}'
            other.save(update_fields=['status', 'archived_by_id', 'archived_at', 'archive_reason'])
            log_bip_status_change(actor.id, other.id, old, other.status,
                                   client_id=other.client_id, version_number=other.version_number)

        old_status = plan.status
        plan.status = BehaviorInterventionPlan.Status.ACTIVE
        plan.activated_by_id = actor.id
        plan.activated_by_name = _full_name(actor)
        plan.activated_at = timezone.now()
        plan.effective_date = effective_date or plan.effective_date or timezone.now().date()
        if review_due_date is not None:
            plan.review_due_date = review_due_date
        plan.save(update_fields=[
            'status', 'activated_by_id', 'activated_by_name', 'activated_at',
            'effective_date', 'review_due_date',
        ])
    log_bip_status_change(actor.id, plan.id, old_status, plan.status,
                           client_id=plan.client_id, version_number=plan.version_number)


def archive_bip_version(plan: BehaviorInterventionPlan, actor, reason: str = '') -> None:
    """Active -> Archived (manual discontinue, not a supersede-by-revision)."""
    if plan.status != BehaviorInterventionPlan.Status.ACTIVE:
        raise HttpError(409, f'Plan must be active to archive (current: {plan.status})')
    old_status = plan.status
    plan.status = BehaviorInterventionPlan.Status.ARCHIVED
    plan.archived_by_id = actor.id
    plan.archived_at = timezone.now()
    plan.archive_reason = reason or 'Discontinued'
    plan.save(update_fields=['status', 'archived_by_id', 'archived_at', 'archive_reason'])
    log_bip_status_change(actor.id, plan.id, old_status, plan.status,
                           client_id=plan.client_id, version_number=plan.version_number)
