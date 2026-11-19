"""Staff-only draft authoring, finalization, signing, revision and activation history.

Progress-note application (Stage 5) is a later stage.
"""
from dataclasses import asdict
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from apps.accounts.permissions import require_permission
from apps.integrations.practice_planner.access import staff_identity
from apps.integrations.practice_planner.contracts import ContentNotFound, DiagnosisQuery, PlannerUnavailable
from apps.integrations.practice_planner.service import service_for_user
from .api import _get_client_or_404
from .models import ClientDiagnosis, TreatmentPlan
from .planner_schemas import (
    ActivationInput, ChildrenInput, DiagnosisInput, DiagnosisUpdate, DraftInput, DraftUpdate,
    DraftDelete, FinalizeInput, LookupInput, ReviseInput, SignInput,
)
from .planner_services import (
    activation_history, diagnosis_output, draft_output, finalize_plan, record_activation,
    revise_plan, save_draft, sign_plan,
)

router = Router(auth=jwt_auth)


def safe_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PermissionDenied:
            raise HttpError(403, 'Practice planner access is not enabled') from None
        except PlannerUnavailable:
            raise HttpError(503, 'Practice planner provider unavailable') from None
        except ContentNotFound:
            raise HttpError(400, 'Selected planner content is not available') from None
        except (ValidationError, ValueError):
            # Validation messages may contain diagnosis/clinical wording. Never echo them.
            raise HttpError(400, 'Invalid planner data or content hierarchy') from None
    return wrapped


def access(request, client_id, *, write=False, diagnosis=False):
    user, _reason = staff_identity(request)
    if user is None:
        raise HttpError(403, 'Active staff access in this organization is required')
    request.user = user
    require_permission(request, 'client_treatment_plan')
    if diagnosis and write:
        require_permission(request, 'clients_edit')
    client = _get_client_or_404(request, client_id)
    service = None
    if write:
        # service_for_user() already enforces the real gate: facility.wiley_enabled,
        # provider readiness, and (for live) facility.wiley_entitled — see
        # access.availability_for_request(). That facility-level entitlement is the
        # actual "pilot with superadmin-enabled facilities" control for Stage 6.
        service = service_for_user(request)
    return client, service


@router.get('/clients/{client_id}/diagnoses')
@safe_errors
def list_diagnoses(request, client_id: int):
    client, _ = access(request, client_id)
    return [diagnosis_output(row) for row in ClientDiagnosis.objects.filter(client=client).order_by('-created_at', '-id')]


@router.post('/clients/{client_id}/diagnoses', response={201: dict})
@safe_errors
@transaction.atomic
def create_diagnosis(request, client_id: int, data: DiagnosisInput):
    client, _ = access(request, client_id, write=True, diagnosis=True)
    if data.supersedes_id and not ClientDiagnosis.objects.filter(pk=data.supersedes_id, client=client).exists():
        raise HttpError(400, 'Superseded diagnosis is not available for this client')
    row = ClientDiagnosis.objects.create(client=client, created_by=request.user, **data.model_dump())
    return 201, diagnosis_output(row)


@router.patch('/clients/{client_id}/diagnoses/{diagnosis_id}')
@safe_errors
@transaction.atomic
def update_diagnosis(request, client_id: int, diagnosis_id: int, data: DiagnosisUpdate):
    client, _ = access(request, client_id, write=True, diagnosis=True)
    try:
        row = ClientDiagnosis.objects.select_for_update().get(pk=diagnosis_id, client=client)
    except ClientDiagnosis.DoesNotExist:
        raise HttpError(404, 'Diagnosis not found') from None
    if row.lock_version != data.expected_lock_version:
        raise HttpError(409, 'This diagnosis changed. Reload before saving.')
    row.clinical_status = data.clinical_status
    if 'effective_to' in data.model_fields_set:
        row.effective_to = data.effective_to
    row.lock_version += 1
    row.save()
    return diagnosis_output(row)


@router.post('/clients/{client_id}/content/lookup')
@safe_errors
def lookup_content(request, client_id: int, data: LookupInput):
    _, service = access(request, client_id, write=True)
    query = DiagnosisQuery(data.system, data.version, data.code)
    return asdict(service.lookup_diagnosis(query, edition=data.edition, language=data.language, cursor=data.cursor))


@router.post('/clients/{client_id}/content/children')
@safe_errors
def content_children(request, client_id: int, data: ChildrenInput):
    _, service = access(request, client_id, write=True)
    return asdict(service.children(data.component_id, edition=data.edition, language=data.language, cursor=data.cursor))


@router.get('/clients/{client_id}/plans')
@safe_errors
def list_drafts(request, client_id: int):
    client, _ = access(request, client_id)
    return [{field: getattr(plan, field) for field in ('id', 'title', 'plan_date', 'status', 'lock_version')}
            for plan in TreatmentPlan.objects.filter(client=client, planning_mode='structured')]


def get_plan(client, plan_id, *, lock=False):
    try:
        query = TreatmentPlan.objects.select_for_update() if lock else TreatmentPlan.objects
        return query.get(pk=plan_id, client=client, planning_mode='structured')
    except TreatmentPlan.DoesNotExist:
        raise HttpError(404, 'Structured plan not found') from None


@router.get('/clients/{client_id}/plans/{plan_id}')
@safe_errors
@transaction.atomic
def read_draft(request, client_id: int, plan_id: int):
    client, _ = access(request, client_id)
    # Match the writer's row lock so the nested document cannot span two saves.
    plan = get_plan(client, plan_id, lock=True)
    return draft_output(plan)


@router.post('/clients/{client_id}/plans', response={201: dict})
@safe_errors
def create_draft(request, client_id: int, data: DraftInput):
    client, service = access(request, client_id, write=True)
    return 201, save_draft(client, data, service, request.user)


@router.put('/clients/{client_id}/plans/{plan_id}')
@safe_errors
def update_draft(request, client_id: int, plan_id: int, data: DraftUpdate):
    client, service = access(request, client_id, write=True)
    return save_draft(client, data, service, request.user, plan_id=plan_id)


@router.delete('/clients/{client_id}/plans/{plan_id}', response={204: None})
@safe_errors
@transaction.atomic
def delete_draft(request, client_id: int, plan_id: int, data: DraftDelete):
    client, _ = access(request, client_id, write=True)
    plan = get_plan(client, plan_id, lock=True)
    if plan.status != 'draft' or plan.is_signed or plan.next_revisions.exists():
        raise HttpError(409, 'Only unsigned drafts without later revisions can be deleted')
    if plan.lock_version != data.expected_lock_version:
        raise HttpError(409, 'This draft changed. Reload before deleting.')
    plan.delete()
    return 204, None


@router.post('/clients/{client_id}/plans/{plan_id}/finalize')
@safe_errors
def finalize_draft(request, client_id: int, plan_id: int, data: FinalizeInput):
    client, _ = access(request, client_id, write=True)
    return finalize_plan(client, plan_id, data, request.user)


@router.post('/clients/{client_id}/plans/{plan_id}/sign')
@safe_errors
def sign_draft(request, client_id: int, plan_id: int, data: SignInput):
    client, _ = access(request, client_id, write=True)
    if request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Only a supervisor or administrator can sign a structured plan')
    return sign_plan(client, plan_id, data, request.user)


@router.post('/clients/{client_id}/plans/{plan_id}/revise', response={201: dict})
@safe_errors
def revise_draft(request, client_id: int, plan_id: int, data: ReviseInput):
    client, _ = access(request, client_id, write=True)
    return 201, revise_plan(client, plan_id, data, request.user)


@router.post('/clients/{client_id}/plans/{plan_id}/activations', response={201: dict})
@safe_errors
def activate_draft(request, client_id: int, plan_id: int, data: ActivationInput):
    client, _ = access(request, client_id, write=True)
    return 201, record_activation(client, plan_id, data, request.user)


@router.get('/clients/{client_id}/plans/{plan_id}/activations')
@safe_errors
def list_activations(request, client_id: int, plan_id: int):
    client, _ = access(request, client_id)
    plan = get_plan(client, plan_id)
    return activation_history(plan)
