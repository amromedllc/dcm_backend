from datetime import date, timedelta
from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from apps.programs.models import Program, Target
from .models import GraphAnnotation
from .schemas import (
    TrialDataPointSchema, BehaviorDataPointSchema,
    ProgramSummarySchema, TargetSummarySchema,
    GraphAnnotationSchema, GraphAnnotationCreateRequest, GraphAnnotationUpdateRequest,
)
from .services import get_trial_data_by_day, get_behavior_data_by_day, get_program_summary

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

@router.get('/analytics/programs/{program_id}/trials', response=list[TrialDataPointSchema])
def program_trial_data(
    request,
    program_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    target_ids: str | None = None,   # comma-separated IDs to filter to specific targets
):
    """
    Daily trial accuracy per target for a program.
    Powers the main program graph. Each target becomes one data series.
    """
    frm, to = _resolve_dates(date_from, date_to)

    qs = Target.objects.filter(program_id=program_id)
    if target_ids:
        ids = [int(i) for i in target_ids.split(',') if i.strip().isdigit()]
        qs = qs.filter(id__in=ids)

    ids_list = list(qs.values_list('id', flat=True))
    return get_trial_data_by_day(ids_list, frm, to)


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
