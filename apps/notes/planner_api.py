"""Stage 5: progress-note autofill preview/apply, signed-note addenda, homework.

Staff-only, gated the same way as the structured plan builder (see
apps.clients.planner_api.access): synthetic mode only until the live
provider is released.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from apps.accounts.permissions import require_permission
from apps.clients.planner_services import resolve_active_plan_for_client
from apps.integrations.practice_planner.access import staff_identity
from apps.integrations.practice_planner.contracts import ContentNotFound, PlannerUnavailable
from apps.integrations.practice_planner.service import service_for_user
from .models import LessonNote
from .planner_schemas import AddendumInput, ApplyInput, HomeworkAssignInput, ObjectiveSelection
from .planner_services import (
    add_addendum, apply_content, assign_homework, complete_homework, list_addenda,
    list_applications, list_homework, note_client, plan_objectives, preview_content,
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
            raise HttpError(400, 'Invalid planner data') from None
    return wrapped


def access(request, note_id):
    user, _reason = staff_identity(request)
    if user is None:
        raise HttpError(403, 'Active staff access in this organization is required')
    request.user = user
    require_permission(request, 'notes_edit')
    try:
        note = LessonNote.objects.select_related('template').get(pk=note_id)
    except LessonNote.DoesNotExist:
        raise HttpError(404, 'Note not found') from None
    if user.role == 'staff' and note.staff_id != user.id:
        raise HttpError(403, 'Access denied')
    service = service_for_user(request)
    return note, service


def resolved_plan(note):
    client = note_client(note)
    if client is None:
        raise HttpError(404, 'This note has no resolvable client')
    plan = resolve_active_plan_for_client(client, note.note_date)
    if plan is None:
        raise HttpError(404, 'No active structured plan is in effect for this client on the note date')
    return client, plan


@router.get('/notes/{note_id}/active-plan')
@safe_errors
def active_plan(request, note_id: int):
    note, _ = access(request, note_id)
    _, plan = resolved_plan(note)
    return {'plan_id': plan.id, 'title': plan.title, 'revision_number': plan.revision_number, 'problems': plan_objectives(plan)}


@router.post('/notes/{note_id}/planner-preview')
@safe_errors
def planner_preview(request, note_id: int, data: ObjectiveSelection):
    note, service = access(request, note_id)
    _, plan = resolved_plan(note)
    return preview_content(plan, data.objective_ids, service)


@router.post('/notes/{note_id}/planner-apply')
@safe_errors
def planner_apply(request, note_id: int, data: ApplyInput):
    note, service = access(request, note_id)
    _, plan = resolved_plan(note)
    return apply_content(note, plan, data.objective_ids, data.field_key, data.mode, service, request.user)


@router.get('/notes/{note_id}/planner-applications')
@safe_errors
def planner_applications(request, note_id: int):
    note, _ = access(request, note_id)
    return list_applications(note)


@router.get('/notes/{note_id}/addenda')
@safe_errors
def addenda(request, note_id: int):
    note, _ = access(request, note_id)
    return list_addenda(note)


@router.post('/notes/{note_id}/addenda', response={201: dict})
@safe_errors
def create_addendum(request, note_id: int, data: AddendumInput):
    note, _ = access(request, note_id)
    return 201, add_addendum(note, request.user, data.text)


@router.post('/notes/{note_id}/homework', response={201: dict})
@safe_errors
def create_homework(request, note_id: int, data: HomeworkAssignInput):
    note, service = access(request, note_id)
    client, plan = resolved_plan(note)
    if not plan.problems.filter(source__component_id=data.problem_component_id, source__edition=data.edition,
                                 source__language=data.language).exists():
        raise HttpError(400, 'That problem is not part of the active plan')
    return 201, assign_homework(client, service, problem_component_id=data.problem_component_id,
        homework_component_id=data.homework_component_id, edition=data.edition, language=data.language,
        actor=request.user, note=note)


@router.get('/notes/{note_id}/homework')
@safe_errors
def note_homework(request, note_id: int):
    note, _ = access(request, note_id)
    client = note_client(note)
    if client is None:
        return []
    return list_homework(client)


@router.post('/homework/{assignment_id}/complete')
@safe_errors
def complete_homework_assignment(request, assignment_id: int):
    from .models import ClientHomeworkAssignment
    user, _reason = staff_identity(request)
    if user is None:
        raise HttpError(403, 'Active staff access in this organization is required')
    request.user = user
    require_permission(request, 'notes_edit')
    from apps.clients.api import _get_accessible_clients
    try:
        assignment = ClientHomeworkAssignment.objects.select_related('source').get(
            pk=assignment_id, client_id__in=_get_accessible_clients(request).values_list('id', flat=True))
    except ClientHomeworkAssignment.DoesNotExist:
        raise HttpError(404, 'Homework assignment not found') from None
    return complete_homework(assignment, request.user)