from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from apps.accounts.permissions import require_permission
from apps.clients.api import _get_accessible_clients, _get_client_or_404
from apps.programs.models import Program
from .models import BehaviorInterventionPlan
from .schemas import (
    BipVersionSchema, BipVersionListSchema,
    BipCreateRequest, BipUpdateRequest, BipReviseRequest,
    BipRejectRequest, BipArchiveRequest, BipActivateRequest,
)
from .services import (
    create_revision, submit_bip_version, reject_bip_version,
    activate_bip_version, archive_bip_version, _assert_editable,
)

router = Router(auth=jwt_auth)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_bip_manager(request, action: str = 'view'):
    require_permission(request, f'bip_{action}')


def _get_plan_or_404(request, version_id: int) -> BehaviorInterventionPlan:
    accessible_client_ids = set(_get_accessible_clients(request).values_list('id', flat=True))
    try:
        plan = (
            BehaviorInterventionPlan.objects
            .select_related('author', 'program')
            .get(id=version_id)
        )
    except BehaviorInterventionPlan.DoesNotExist:
        raise HttpError(404, 'Behavior plan version not found')
    if plan.program.external_client_id not in accessible_client_ids:
        raise HttpError(404, 'Behavior plan version not found')
    return plan


def _get_behavior_reduction_program_or_404(request, client_id: int, program_id: int) -> Program:
    try:
        program = Program.objects.get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    if program.external_client_id != client_id:
        raise HttpError(400, 'Program does not belong to this client')
    if program.category != Program.Category.BEHAVIOR_REDUCTION:
        raise HttpError(400, 'Behavior plans can only be created for behavior-reduction programs')
    return program


def _serialize(plan: BehaviorInterventionPlan) -> dict:
    return {
        'id': plan.id,
        'client_id': plan.client_id,
        'program_id': plan.program_id,
        'program_name': plan.program.name,
        'version_number': plan.version_number,
        'previous_version_id': plan.previous_version_id,
        'next_version_id': getattr(plan, 'next_version', None) and plan.next_version.id,
        'title': plan.title,
        'target_behavior_name': plan.target_behavior_name,
        'target_behavior_definition': plan.target_behavior_definition,
        'baseline_summary': plan.baseline_summary,
        'hypothesized_function': plan.hypothesized_function,
        'function_hypothesis_statement': plan.function_hypothesis_statement,
        'antecedent_strategies': plan.antecedent_strategies,
        'replacement_behaviors': plan.replacement_behaviors,
        'teaching_procedures': plan.teaching_procedures,
        'reinforcement_strategies': plan.reinforcement_strategies,
        'response_procedures': plan.response_procedures,
        'crisis_plan': plan.crisis_plan,
        'safety_precautions': plan.safety_precautions,
        'data_collection_plan': plan.data_collection_plan,
        'generalization_plan': plan.generalization_plan,
        'status': plan.status,
        'author_id': plan.author_id,
        'author_name': plan.author_name,
        'effective_date': plan.effective_date,
        'review_due_date': plan.review_due_date,
        'submitted_at': plan.submitted_at,
        'activated_by_name': plan.activated_by_name,
        'activated_by_id': plan.activated_by_id,
        'activated_at': plan.activated_at,
        'rejected_by_id': plan.rejected_by_id,
        'rejected_at': plan.rejected_at,
        'rejection_reason': plan.rejection_reason,
        'archived_by_id': plan.archived_by_id,
        'archived_at': plan.archived_at,
        'archive_reason': plan.archive_reason,
        'created_at': plan.created_at,
        'updated_at': plan.updated_at,
    }


# ---------------------------------------------------------------------------
# Read — active lookups registered before the {version_id} routes
# ---------------------------------------------------------------------------

@router.get('/bip-versions/active', response=BipVersionSchema | None)
def get_active_bip_version(request, program_id: int):
    """The active plan for one specific behavior-reduction program, or null."""
    _require_bip_manager(request, 'view')
    plan = (
        BehaviorInterventionPlan.objects
        .select_related('author', 'program')
        .filter(program_id=program_id, status=BehaviorInterventionPlan.Status.ACTIVE)
        .first()
    )
    if plan and plan.program.external_client_id not in set(_get_accessible_clients(request).values_list('id', flat=True)):
        return None
    return _serialize(plan) if plan else None


@router.get('/bip-versions/active-list', response=list[BipVersionSchema])
def list_active_bip_versions(request, client_id: int):
    """Every currently-active plan across all of this client's programs —
    a client can have several (one per problem behavior)."""
    _require_bip_manager(request, 'view')
    _get_client_or_404(request, client_id)
    qs = (
        BehaviorInterventionPlan.objects
        .select_related('author', 'program')
        .filter(program__external_client_id=client_id, status=BehaviorInterventionPlan.Status.ACTIVE)
    )
    return [_serialize(plan) for plan in qs]


@router.get('/bip-versions', response=list[BipVersionListSchema])
def list_bip_versions(request, client_id: int, program_id: int | None = None, status: str | None = None):
    _require_bip_manager(request, 'view')
    _get_client_or_404(request, client_id)
    qs = (
        BehaviorInterventionPlan.objects
        .select_related('author', 'program')
        .filter(program__external_client_id=client_id)
    )
    if program_id:
        qs = qs.filter(program_id=program_id)
    if status:
        qs = qs.filter(status=status)
    return [_serialize(plan) for plan in qs]


@router.post('/bip-versions', response={201: BipVersionSchema})
def create_bip_version(request, data: BipCreateRequest):
    _require_bip_manager(request, 'create')
    _get_client_or_404(request, data.client_id)
    program = _get_behavior_reduction_program_or_404(request, data.client_id, data.program_id)

    existing = (
        BehaviorInterventionPlan.objects
        .filter(program_id=program.id)
        .exclude(status=BehaviorInterventionPlan.Status.ARCHIVED)
        .exists()
    )
    if existing:
        raise HttpError(409, 'A non-archived plan already exists for this program — use /revise instead')

    payload = data.dict()
    payload.pop('client_id')
    payload.pop('program_id')
    plan = BehaviorInterventionPlan.objects.create(
        program=program,
        version_number=1,
        author=request.user,
        author_name=f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.email,
        created_by=request.user,
        **payload,
    )
    return 201, _serialize(plan)


@router.get('/bip-versions/{version_id}', response=BipVersionSchema)
def get_bip_version(request, version_id: int):
    _require_bip_manager(request, 'view')
    plan = _get_plan_or_404(request, version_id)
    return _serialize(plan)


@router.patch('/bip-versions/{version_id}', response=BipVersionSchema)
def update_bip_version(request, version_id: int, data: BipUpdateRequest):
    _require_bip_manager(request, 'edit')
    plan = _get_plan_or_404(request, version_id)
    _assert_editable(plan)
    for field, value in data.dict(exclude_none=True).items():
        setattr(plan, field, value)
    plan.save()
    plan.refresh_from_db()
    return _serialize(plan)


@router.delete('/bip-versions/{version_id}', response={204: None})
def delete_bip_version(request, version_id: int):
    _require_bip_manager(request, 'delete')
    plan = _get_plan_or_404(request, version_id)
    if plan.status not in (BehaviorInterventionPlan.Status.DRAFT, BehaviorInterventionPlan.Status.REJECTED):
        raise HttpError(409, 'Only draft or rejected plan versions can be deleted')
    plan.delete()
    return 204, None


# ---------------------------------------------------------------------------
# Workflow — revise / submit / reject / activate / archive
# ---------------------------------------------------------------------------

@router.post('/bip-versions/{version_id}/revise', response={201: BipVersionSchema})
def revise_bip_version(request, version_id: int, data: BipReviseRequest):
    _require_bip_manager(request, 'create')
    source = _get_plan_or_404(request, version_id)
    revision = create_revision(source, request.user, title=data.title)
    return 201, _serialize(revision)


@router.post('/bip-versions/{version_id}/submit', response=BipVersionSchema)
def submit(request, version_id: int):
    _require_bip_manager(request, 'edit')
    plan = _get_plan_or_404(request, version_id)
    submit_bip_version(plan, request.user)
    plan.refresh_from_db()
    return _serialize(plan)


@router.post('/bip-versions/{version_id}/reject', response=BipVersionSchema)
def reject(request, version_id: int, data: BipRejectRequest):
    _require_bip_manager(request, 'activate')
    plan = _get_plan_or_404(request, version_id)
    reject_bip_version(plan, request.user, data.reason)
    plan.refresh_from_db()
    return _serialize(plan)


@router.post('/bip-versions/{version_id}/activate', response=BipVersionSchema)
def activate(request, version_id: int, data: BipActivateRequest):
    _require_bip_manager(request, 'activate')
    plan = _get_plan_or_404(request, version_id)
    activate_bip_version(plan, request.user, effective_date=data.effective_date, review_due_date=data.review_due_date)
    plan.refresh_from_db()
    return _serialize(plan)


@router.post('/bip-versions/{version_id}/archive', response=BipVersionSchema)
def archive(request, version_id: int, data: BipArchiveRequest):
    _require_bip_manager(request, 'activate')
    plan = _get_plan_or_404(request, version_id)
    archive_bip_version(plan, request.user, data.reason)
    plan.refresh_from_db()
    return _serialize(plan)
