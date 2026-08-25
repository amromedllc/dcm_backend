from datetime import date, timedelta
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from apps.programs.models import Program, Target, ProgramModule
from .models import GraphAnnotation, ClientAnnotation
from .schemas import (
    TrialDataPointSchema, BehaviorDataPointSchema,
    ProgramSummarySchema, ModuleSummarySchema, TargetSummarySchema,
    GraphAnnotationSchema, GraphAnnotationCreateRequest, GraphAnnotationUpdateRequest,
    ClientAnnotationSchema, ClientAnnotationCreateRequest, ClientAnnotationUpdateRequest,
)
from .services import (
    get_trial_data_by_day, get_behavior_data_by_day, get_program_summary, get_module_summary,
    get_client_progress_report, get_client_progress_overview,
)

router = Router(auth=jwt_auth)

_DEFAULT_DAYS = 90


def _resolve_dates(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    to = date_to or timezone.now().date()
    frm = date_from or (to - timedelta(days=_DEFAULT_DAYS))
    return frm, to


def _require_supervisor(request):
    if request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')


# ---------------------------------------------------------------------------
# Trial graph data
# ---------------------------------------------------------------------------

_VALID_GROUP_BY = {'target', 'prompt_level', 'user'}


@router.get('/analytics/programs/{program_id}/trials', response=list[TrialDataPointSchema])
def program_trial_data(
    request,
    program_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    target_ids: str | None = None,   # comma-separated IDs to filter to specific targets
    group_by: str = 'target',        # 'target' (default) | 'prompt_level' | 'user'
):
    """
    Daily trial accuracy for a program, grouped into data series by `group_by`.
    Powers the main program graph.
    """
    if group_by not in _VALID_GROUP_BY:
        raise HttpError(400, f'Invalid group_by: {group_by}')
    frm, to = _resolve_dates(date_from, date_to)

    qs = Target.objects.filter(program_id=program_id)
    if target_ids:
        ids = [int(i) for i in target_ids.split(',') if i.strip().isdigit()]
        qs = qs.filter(id__in=ids)

    ids_list = list(qs.values_list('id', flat=True))
    return get_trial_data_by_day(ids_list, frm, to, group_by=group_by)


@router.get('/analytics/targets/{target_id}/trials', response=list[TrialDataPointSchema])
def target_trial_data(
    request,
    target_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
):
    """Single-target drill-down. Powers the target detail graph."""
    frm, to = _resolve_dates(date_from, date_to)
    return get_trial_data_by_day([target_id], frm, to)


# ---------------------------------------------------------------------------
# Behavior graph data
# ---------------------------------------------------------------------------

@router.get('/analytics/programs/{program_id}/behaviors', response=list[BehaviorDataPointSchema])
def program_behavior_data(
    request,
    program_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
):
    """Daily behavior frequency and duration per target. Powers behavior reduction graphs."""
    frm, to = _resolve_dates(date_from, date_to)
    target_ids = list(
        Target.objects.filter(program_id=program_id).values_list('id', flat=True)
    )
    return get_behavior_data_by_day(target_ids, frm, to)


# ---------------------------------------------------------------------------
# Program summary — all target cards in one request
# ---------------------------------------------------------------------------

@router.get('/analytics/programs/{program_id}/summary', response=ProgramSummarySchema)
def program_summary(
    request,
    program_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
):
    """
    Per-target summary for the program detail page.
    Returns status, total trials, avg accuracy, and trend for every target.
    """
    frm, to = _resolve_dates(date_from, date_to)
    targets = get_program_summary(program_id, frm, to)
    return {
        'program_id': program_id,
        'date_from': frm,
        'date_to': to,
        'targets': targets,
    }


# ---------------------------------------------------------------------------
# Client progress report — full aggregated report in one call
# ---------------------------------------------------------------------------

@router.get('/analytics/clients/{client_id}/report')
def client_progress_report(
    request,
    client_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
):
    """
    Full client progress report in a single request.
    Returns session counts, program list, per-target mastery rates and trends.
    Replaces N+1 calls from the frontend report page.
    """
    from apps.accounts.permissions import require_permission
    require_permission(request, 'client_report')
    frm, to = _resolve_dates(date_from, date_to)
    return get_client_progress_report(client_id, frm, to)


# ---------------------------------------------------------------------------
# Client progress overview — cumulative mastery timeline + per-program rollup
# across every program at once (powers the top-level Progress screen)
# ---------------------------------------------------------------------------

@router.get('/analytics/clients/{client_id}/progress-overview')
def client_progress_overview(request, client_id: int):
    """
    Consolidated client progress: raw mastery events (target/program/
    treatment-area/tags/status + timestamp) plus a per-program rollup
    (status counts, avg trials/sessions to mastery), across every program
    for the client in a single request. All-time — the frontend buckets,
    filters, and toggles cumulative vs. per-period client-side.
    """
    from apps.accounts.permissions import require_permission
    require_permission(request, 'client_progress')
    return get_client_progress_overview(client_id)


# ---------------------------------------------------------------------------
# Client-level annotations (Progress screen mastery chart)
# ---------------------------------------------------------------------------

@router.get('/analytics/clients/{client_id}/annotations', response=list[ClientAnnotationSchema])
def list_client_annotations(request, client_id: int):
    return list(ClientAnnotation.objects.filter(external_client_id=client_id))


@router.post('/analytics/clients/{client_id}/annotations', response={201: ClientAnnotationSchema})
def create_client_annotation(request, client_id: int, data: ClientAnnotationCreateRequest):
    _require_supervisor(request)
    annotation = ClientAnnotation.objects.create(
        external_client_id=client_id,
        created_by=request.user,
        **data.dict(),
    )
    return 201, annotation


@router.patch('/analytics/client-annotations/{annotation_id}', response=ClientAnnotationSchema)
def update_client_annotation(request, annotation_id: int, data: ClientAnnotationUpdateRequest):
    _require_supervisor(request)
    try:
        annotation = ClientAnnotation.objects.get(id=annotation_id)
    except ClientAnnotation.DoesNotExist:
        raise HttpError(404, 'Annotation not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(annotation, field, value)
    annotation.save()
    return annotation


@router.delete('/analytics/client-annotations/{annotation_id}', response={204: None})
def delete_client_annotation(request, annotation_id: int):
    _require_supervisor(request)
    try:
        ClientAnnotation.objects.get(id=annotation_id).delete()
    except ClientAnnotation.DoesNotExist:
        raise HttpError(404, 'Annotation not found')
    return 204, None


# ---------------------------------------------------------------------------
# Module summary
# ---------------------------------------------------------------------------

@router.get('/analytics/programs/{program_id}/modules/{module_id}/summary', response=ModuleSummarySchema)
def module_summary(
    request,
    program_id: int,
    module_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
):
    """Per-target summary scoped to a single module."""
    frm, to = _resolve_dates(date_from, date_to)
    try:
        mod = ProgramModule.objects.get(id=module_id, program_id=program_id)
    except ProgramModule.DoesNotExist:
        raise HttpError(404, 'Module not found')
    targets = get_module_summary(module_id, frm, to)
    return {
        'module_id': module_id,
        'module_name': mod.name,
        'date_from': frm,
        'date_to': to,
        'targets': targets,
    }


# ---------------------------------------------------------------------------
# Graph annotations
# ---------------------------------------------------------------------------

@router.get('/analytics/programs/{program_id}/annotations', response=list[GraphAnnotationSchema])
def list_annotations(request, program_id: int, target_id: int | None = None):
    qs = GraphAnnotation.objects.filter(program_id=program_id)
    if target_id is not None:
        qs = qs.filter(target_id=target_id)
    return list(qs)


@router.post('/analytics/programs/{program_id}/annotations', response={201: GraphAnnotationSchema})
def create_annotation(request, program_id: int, data: GraphAnnotationCreateRequest):
    _require_supervisor(request)
    try:
        Program.objects.get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')

    if data.annotation_type == 'phase_range' and not data.end_date:
        raise HttpError(400, 'phase_range annotations require an end_date')
    if data.annotation_type != 'phase_range' and data.end_date:
        raise HttpError(400, 'end_date is only valid for phase_range annotations')

    annotation = GraphAnnotation.objects.create(
        program_id=program_id,
        created_by=request.user,
        **data.dict(),
    )
    return 201, annotation


@router.patch('/analytics/annotations/{annotation_id}', response=GraphAnnotationSchema)
def update_annotation(request, annotation_id: int, data: GraphAnnotationUpdateRequest):
    _require_supervisor(request)
    try:
        annotation = GraphAnnotation.objects.get(id=annotation_id)
    except GraphAnnotation.DoesNotExist:
        raise HttpError(404, 'Annotation not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(annotation, field, value)
    annotation.save()
    return annotation


@router.delete('/analytics/annotations/{annotation_id}', response={204: None})
def delete_annotation(request, annotation_id: int):
    _require_supervisor(request)
    try:
        GraphAnnotation.objects.get(id=annotation_id).delete()
    except GraphAnnotation.DoesNotExist:
        raise HttpError(404, 'Annotation not found')
    return 204, None
