from ninja import Router
from ninja.errors import HttpError
from django.utils import timezone

from apps.accounts.auth import jwt_auth
from .models import (
    Program, Target, PromptingTemplate, MasteryTemplate,
    WorkflowTemplate, MaintenanceSchedule,
    Lesson, LessonProgram,
    TreatmentArea, ProgramTag, ProgramDataField, TargetStatus,
    TargetStatusChange,
)
from .schemas import (
    ProgramSchema, ProgramListSchema, ProgramCreateRequest, ProgramUpdateRequest,
    TargetSchema, TargetCreateRequest, TargetUpdateRequest,
    BulkUpdateTargetsRequest, BulkUpdateResult, ReorderTargetsRequest,
    PromptingTemplateSchema, PromptingTemplateCreateRequest, PromptingTemplateUpdateRequest,
    MasteryTemplateSchema, MasteryTemplateCreateRequest, MasteryTemplateUpdateRequest,
    WorkflowTemplateSchema, WorkflowTemplateCreateRequest, WorkflowTemplateUpdateRequest,
    MaintenanceScheduleSchema, MaintenanceScheduleCreateRequest, MaintenanceScheduleUpdateRequest,
    LessonSchema, LessonCreateRequest, LessonUpdateRequest, AddProgramToLessonRequest,
    LessonProgramSchema,
    OrgProgramSchema, OrgProgramCreateRequest, AssignOrgProgramRequest,
    TreatmentAreaSchema, TreatmentAreaRequest,
    ProgramTagSchema, ProgramTagRequest,
    ProgramDataFieldSchema, ProgramDataFieldRequest,
    TargetStatusChangeSchema,
    TargetStatusSchema, TargetStatusRequest, TargetStatusUpdateRequest,
)

router = Router(auth=jwt_auth)


def _require_supervisor(request):
    if request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')


def _require_admin(request):
    if request.user.role != 'admin':
        raise HttpError(403, 'Admin access required')


def _serialize_program(program: Program, include_targets: bool = False) -> dict:
    data = {
        'id': program.id,
        'client_id': program.external_client_id,
        'name': program.name,
        'category': program.category,
        'status': program.status,
        'phase': program.phase,
        'treatment_area': program.treatment_area,
        'tags': program.tags,
        'baseline_notes': program.baseline_notes,
        'objective': program.objective,
        'instructions': program.instructions,
        'workflow_template_id': program.workflow_template_id,
        'maintenance_schedule_id': program.maintenance_schedule_id,
        'display_order': program.display_order,
        'archived_at': program.archived_at,
        'created_at': program.created_at,
        'updated_at': program.updated_at,
    }
    if include_targets:
        data['targets'] = list(program.targets.all().values(
            'id', 'name', 'status', 'display_order', 'is_visible_to_staff'
        ))
    return data


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------

@router.get('/programs', response=list[ProgramListSchema])
def list_programs(request, client_id: int, category: str | None = None, status: str | None = None):
    qs = Program.objects.filter(external_client_id=client_id).exclude(status='archived')
    if category:
        qs = qs.filter(category=category)
    if status:
        qs = qs.filter(status=status)
    result = []
    for p in qs.prefetch_related('targets'):
        targets = list(p.targets.all())
        status_counts: dict[str, int] = {}
        for t in targets:
            status_counts[t.status] = status_counts.get(t.status, 0) + 1
        result.append({
            **_serialize_program(p),
            'target_count': len(targets),
            'target_status_counts': status_counts,
        })
    return result


@router.post('/programs', response={201: ProgramSchema})
def create_program(request, data: ProgramCreateRequest):
    _require_supervisor(request)
    program = Program.objects.create(
        external_client_id=data.client_id,
        name=data.name,
        category=data.category,
        phase=data.phase,
        treatment_area=data.treatment_area,
        tags=data.tags,
        baseline_notes=data.baseline_notes,
        objective=data.objective,
        instructions=data.instructions,
        workflow_template_id=data.workflow_template_id,
        maintenance_schedule_id=data.maintenance_schedule_id,
        display_order=data.display_order,
        created_by=request.user,
    )
    return 201, {**_serialize_program(program), 'targets': []}


@router.get('/programs/{program_id}', response=ProgramSchema)
def get_program(request, program_id: int):
    try:
        program = Program.objects.prefetch_related('targets').get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    return {**_serialize_program(program, include_targets=True)}


@router.patch('/programs/{program_id}', response=ProgramSchema)
def update_program(request, program_id: int, data: ProgramUpdateRequest):
    _require_supervisor(request)
    try:
        program = Program.objects.get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    updates = data.dict(exclude_none=True)
    for field, value in updates.items():
        setattr(program, field, value)
    program.save()
    if 'workflow_template_id' in updates:
        program.targets.update(workflow_template_id=program.workflow_template_id)
    return {**_serialize_program(program, include_targets=True)}


@router.delete('/programs/{program_id}', response={204: None})
def archive_program(request, program_id: int):
    _require_supervisor(request)
    try:
        program = Program.objects.get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    program.status = Program.Status.ARCHIVED
    program.archived_at = timezone.now()
    program.save(update_fields=['status', 'archived_at'])
    return 204, None


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

@router.get('/programs/{program_id}/targets', response=list[TargetSchema])
def list_targets(request, program_id: int, staff_view: bool = False):
    try:
        program = Program.objects.get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    qs = program.targets.all()
    if staff_view:
        qs = qs.visible_to_staff()
    return list(qs)


@router.post('/programs/{program_id}/targets', response={201: TargetSchema})
def create_target(request, program_id: int, data: TargetCreateRequest):
    _require_supervisor(request)
    try:
        program = Program.objects.get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    target_data = data.dict()
    if program.workflow_template_id and not target_data.get('workflow_template_id'):
        target_data['workflow_template_id'] = program.workflow_template_id
    if not target_data.get('status'):
        default_status = TargetStatus.objects.filter(is_default=True).first()
        target_data['status'] = default_status.key if default_status else Target.Status.WAITING
    target = Target.objects.create(
        program=program,
        created_by=request.user,
        **target_data,
    )
    return 201, target


@router.get('/targets/{target_id}', response=TargetSchema)
def get_target(request, target_id: int):
    try:
        return Target.objects.get(id=target_id)
    except Target.DoesNotExist:
        raise HttpError(404, 'Target not found')


@router.patch('/targets/{target_id}', response=TargetSchema)
def update_target(request, target_id: int, data: TargetUpdateRequest):
    _require_supervisor(request)
    try:
        target = Target.objects.get(id=target_id)
    except Target.DoesNotExist:
        raise HttpError(404, 'Target not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(target, field, value)
    target.save()
    return target


@router.delete('/targets/{target_id}', response={204: None})
def delete_target(request, target_id: int):
    _require_supervisor(request)
    deleted, _ = Target.objects.filter(id=target_id).delete()
    if not deleted:
        raise HttpError(404, 'Target not found')
    return 204, None


@router.get('/targets/{target_id}/history', response=list[TargetStatusChangeSchema])
def target_history(request, target_id: int):
    qs = (
        TargetStatusChange.objects
        .filter(target_id=target_id)
        .select_related('created_by')
        .order_by('-created_at')[:50]
    )
    result = []
    for entry in qs:
        changed_by = None
        if entry.created_by_id:
            u = entry.created_by
            changed_by = (
                f'{u.first_name} {u.last_name}'.strip()
                or u.email
            )
        result.append(TargetStatusChangeSchema(
            id=entry.id,
            from_status=entry.from_status,
            to_status=entry.to_status,
            trigger=entry.trigger,
            session_run_id=entry.session_run_id,
            changed_by=changed_by,
            created_at=entry.created_at,
        ))
    return result



# FK id fields bulk_update_targets accepts, mapped to the org-scoped manager
# each must be re-validated against — QuerySet.update() skips Target.save()'s
# _validate_cross_org_fks(), so a template id belonging to another org (ids
# are enumerable) would otherwise be written with no check at all.
_BULK_UPDATE_FK_MODELS = {
    'prompting_template_id': PromptingTemplate,
    'mastery_template_id': MasteryTemplate,
    'workflow_template_id': WorkflowTemplate,
    'maintenance_schedule_id': MaintenanceSchedule,
}


@router.post('/programs/{program_id}/targets/bulk-update', response=BulkUpdateResult)
def bulk_update_targets(request, program_id: int, data: BulkUpdateTargetsRequest):
    """Update specific fields across multiple targets without touching unspecified fields."""
    _require_supervisor(request)
    updates = data.dict(exclude={'target_ids'}, exclude_none=True)
    if not updates:
        raise HttpError(400, 'No fields to update were provided')

    for field_name, model in _BULK_UPDATE_FK_MODELS.items():
        fk_id = updates.get(field_name)
        if fk_id is not None and not model.objects.filter(id=fk_id).exists():
            raise HttpError(400, f'Invalid {field_name}: {fk_id}')

    updates['updated_at'] = timezone.now()
    updated = Target.objects.filter(
        id__in=data.target_ids,
        program_id=program_id,
    ).update(**updates)
    if updated == 0:
        raise HttpError(400, 'No matching targets found for this program')
    return BulkUpdateResult(updated_count=updated, target_ids=data.target_ids)


@router.post('/programs/{program_id}/targets/reorder', response={200: None})
def reorder_targets(request, program_id: int, data: ReorderTargetsRequest):
    """Set display_order on targets based on the submitted ordered list of IDs."""
    _require_supervisor(request)
    for order, target_id in enumerate(data.ordered_ids):
        Target.objects.filter(id=target_id, program_id=program_id).update(display_order=order)
    return 200, None


# ---------------------------------------------------------------------------
# Prompting templates
# ---------------------------------------------------------------------------

@router.get('/programs/templates/prompting', response=list[PromptingTemplateSchema])
def list_prompting_templates(request):
    return list(PromptingTemplate.objects.all())


@router.post('/programs/templates/prompting', response={201: PromptingTemplateSchema})
def create_prompting_template(request, data: PromptingTemplateCreateRequest):
    _require_supervisor(request)
    template = PromptingTemplate.objects.create(created_by=request.user, **data.dict())
    return 201, template


@router.patch('/programs/templates/prompting/{template_id}', response=PromptingTemplateSchema)
def update_prompting_template(request, template_id: int, data: PromptingTemplateUpdateRequest):
    _require_supervisor(request)
    try:
        template = PromptingTemplate.objects.get(id=template_id)
    except PromptingTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(template, field, value)
    template.save()
    return template


@router.delete('/programs/templates/prompting/{template_id}', response={204: None})
def delete_prompting_template(request, template_id: int):
    _require_supervisor(request)
    try:
        PromptingTemplate.objects.get(id=template_id).delete()
    except PromptingTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')
    return 204, None


# ---------------------------------------------------------------------------
# Mastery templates
# ---------------------------------------------------------------------------

@router.get('/programs/templates/mastery', response=list[MasteryTemplateSchema])
def list_mastery_templates(request):
    return list(MasteryTemplate.objects.all())


@router.post('/programs/templates/mastery', response={201: MasteryTemplateSchema})
def create_mastery_template(request, data: MasteryTemplateCreateRequest):
    _require_supervisor(request)
    template = MasteryTemplate.objects.create(created_by=request.user, **data.dict())
    return 201, template


@router.patch('/programs/templates/mastery/{template_id}', response=MasteryTemplateSchema)
def update_mastery_template(request, template_id: int, data: MasteryTemplateUpdateRequest):
    _require_supervisor(request)
    try:
        template = MasteryTemplate.objects.get(id=template_id)
    except MasteryTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(template, field, value)
    template.save()
    return template


@router.delete('/programs/templates/mastery/{template_id}', response={204: None})
def delete_mastery_template(request, template_id: int):
    _require_supervisor(request)
    try:
        MasteryTemplate.objects.get(id=template_id).delete()
    except MasteryTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')
    return 204, None


# ---------------------------------------------------------------------------
# Workflow templates
# ---------------------------------------------------------------------------

@router.get('/programs/templates/workflow', response=list[WorkflowTemplateSchema])
def list_workflow_templates(request, include_inactive: bool = False):
    qs = WorkflowTemplate.objects.all()
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs)


@router.post('/programs/templates/workflow', response={201: WorkflowTemplateSchema})
def create_workflow_template(request, data: WorkflowTemplateCreateRequest):
    _require_supervisor(request)
    template = WorkflowTemplate.objects.create(created_by=request.user, **data.dict())
    return 201, template


@router.get('/programs/templates/workflow/{template_id}', response=WorkflowTemplateSchema)
def get_workflow_template(request, template_id: int):
    try:
        return WorkflowTemplate.objects.get(id=template_id)
    except WorkflowTemplate.DoesNotExist:
        raise HttpError(404, 'Workflow template not found')


@router.patch('/programs/templates/workflow/{template_id}', response=WorkflowTemplateSchema)
def update_workflow_template(request, template_id: int, data: WorkflowTemplateUpdateRequest):
    _require_supervisor(request)
    try:
        template = WorkflowTemplate.objects.get(id=template_id)
    except WorkflowTemplate.DoesNotExist:
        raise HttpError(404, 'Workflow template not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(template, field, value)
    template.save()
    return template


@router.delete('/programs/templates/workflow/{template_id}', response={204: None})
def delete_workflow_template(request, template_id: int):
    _require_supervisor(request)
    try:
        WorkflowTemplate.objects.get(id=template_id).delete()
    except WorkflowTemplate.DoesNotExist:
        raise HttpError(404, 'Workflow template not found')
    return 204, None


# ---------------------------------------------------------------------------
# Maintenance schedules
# ---------------------------------------------------------------------------

@router.get('/programs/templates/maintenance', response=list[MaintenanceScheduleSchema])
def list_maintenance_schedules(request):
    return list(MaintenanceSchedule.objects.all())


@router.post('/programs/templates/maintenance', response={201: MaintenanceScheduleSchema})
def create_maintenance_schedule(request, data: MaintenanceScheduleCreateRequest):
    _require_supervisor(request)
    schedule = MaintenanceSchedule.objects.create(created_by=request.user, **data.dict())
    return 201, schedule


@router.get('/programs/templates/maintenance/{schedule_id}', response=MaintenanceScheduleSchema)
def get_maintenance_schedule(request, schedule_id: int):
    try:
        return MaintenanceSchedule.objects.get(id=schedule_id)
    except MaintenanceSchedule.DoesNotExist:
        raise HttpError(404, 'Maintenance schedule not found')


@router.patch('/programs/templates/maintenance/{schedule_id}', response=MaintenanceScheduleSchema)
def update_maintenance_schedule(request, schedule_id: int, data: MaintenanceScheduleUpdateRequest):
    _require_supervisor(request)
    try:
        schedule = MaintenanceSchedule.objects.get(id=schedule_id)
    except MaintenanceSchedule.DoesNotExist:
        raise HttpError(404, 'Maintenance schedule not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(schedule, field, value)
    schedule.save()
    return schedule


@router.delete('/programs/templates/maintenance/{schedule_id}', response={204: None})
def delete_maintenance_schedule(request, schedule_id: int):
    _require_supervisor(request)
    try:
        MaintenanceSchedule.objects.get(id=schedule_id).delete()
    except MaintenanceSchedule.DoesNotExist:
        raise HttpError(404, 'Maintenance schedule not found')
    return 204, None


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------

def _serialize_lesson(lesson: Lesson) -> dict:
    programs = [
        {
            'id': lp.id,
            'program_id': lp.program_id,
            'program_name': lp.program.name,
            'display_order': lp.display_order,
        }
        for lp in lesson.lesson_programs.select_related('program').all()
    ]
    return {
        'id': lesson.id,
        'client_id': lesson.external_client_id,
        'name': lesson.name,
        'lesson_type': lesson.lesson_type,
        'is_active': lesson.is_active,
        'programs': programs,
        'created_at': lesson.created_at,
        'updated_at': lesson.updated_at,
    }


@router.get('/lessons', response=list[LessonSchema])
def list_lessons(request, client_id: int):
    lessons = Lesson.objects.filter(external_client_id=client_id, is_active=True)
    return [_serialize_lesson(l) for l in lessons]


@router.post('/lessons', response={201: LessonSchema})
def create_lesson(request, data: LessonCreateRequest):
    _require_supervisor(request)
    lesson = Lesson.objects.create(
        external_client_id=data.client_id,
        name=data.name,
        lesson_type=data.lesson_type,
        created_by=request.user,
    )
    for order, program_id in enumerate(data.program_ids):
        LessonProgram.objects.create(lesson=lesson, program_id=program_id, display_order=order)
    return 201, _serialize_lesson(lesson)


@router.get('/lessons/{lesson_id}', response=LessonSchema)
def get_lesson(request, lesson_id: int):
    try:
        return _serialize_lesson(Lesson.objects.get(id=lesson_id))
    except Lesson.DoesNotExist:
        raise HttpError(404, 'Lesson not found')


@router.patch('/lessons/{lesson_id}', response=LessonSchema)
def update_lesson(request, lesson_id: int, data: LessonUpdateRequest):
    _require_supervisor(request)
    try:
        lesson = Lesson.objects.get(id=lesson_id)
    except Lesson.DoesNotExist:
        raise HttpError(404, 'Lesson not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(lesson, field, value)
    lesson.save()
    return _serialize_lesson(lesson)


@router.post('/lessons/{lesson_id}/programs', response={201: LessonProgramSchema})
def add_program_to_lesson(request, lesson_id: int, data: AddProgramToLessonRequest):
    _require_supervisor(request)
    try:
        lesson = Lesson.objects.get(id=lesson_id)
    except Lesson.DoesNotExist:
        raise HttpError(404, 'Lesson not found')
    lp, _ = LessonProgram.objects.get_or_create(
        lesson=lesson,
        program_id=data.program_id,
        defaults={'display_order': data.display_order},
    )
    return 201, {
        'id': lp.id,
        'program_id': lp.program_id,
        'program_name': lp.program.name,
        'display_order': lp.display_order,
    }


@router.delete('/lessons/{lesson_id}/programs/{program_id}', response={204: None})
def remove_program_from_lesson(request, lesson_id: int, program_id: int):
    _require_supervisor(request)
    LessonProgram.objects.filter(lesson_id=lesson_id, program_id=program_id).delete()
    return 204, None


# ---------------------------------------------------------------------------
# Org-level program library (facility-wide templates)
# ---------------------------------------------------------------------------

def _serialize_org_program(program: Program, include_targets: bool = False) -> dict:
    targets = list(program.targets.all().values(
        'id', 'name', 'status', 'display_order', 'is_visible_to_staff',
    )) if include_targets else []
    return {
        'id': program.id,
        'is_template': program.is_template,
        'name': program.name,
        'category': program.category,
        'status': program.status,
        'phase': program.phase,
        'treatment_area': program.treatment_area,
        'tags': program.tags,
        'objective': program.objective,
        'instructions': program.instructions,
        'display_order': program.display_order,
        'target_count': program.targets.count(),
        'targets': targets,
        'created_at': program.created_at,
        'updated_at': program.updated_at,
    }


def _org_qs(request):
    """Return org-template programs scoped to the authenticated user's facility."""
    return Program.objects.filter(
        is_template=True,
        external_client_id__isnull=True,
        created_by__external_admin_id=request.user.external_admin_id,
    )


@router.get('/org-programs', response=list[OrgProgramSchema])
def list_org_programs(request, category: str | None = None, status: str | None = None):
    qs = _org_qs(request).exclude(status='archived')
    if category:
        qs = qs.filter(category=category)
    if status:
        qs = qs.filter(status=status)
    return [_serialize_org_program(p) for p in qs.prefetch_related('targets')]


@router.post('/org-programs', response={201: OrgProgramSchema})
def create_org_program(request, data: OrgProgramCreateRequest):
    _require_admin(request)
    program = Program.objects.create(
        is_template=True,
        external_client_id=None,
        name=data.name,
        category=data.category,
        phase=data.phase,
        treatment_area=data.treatment_area,
        tags=data.tags,
        objective=data.objective,
        instructions=data.instructions,
        workflow_template_id=data.workflow_template_id,
        display_order=data.display_order,
        created_by=request.user,
    )
    return 201, _serialize_org_program(program, include_targets=True)


@router.get('/org-programs/{program_id}', response=OrgProgramSchema)
def get_org_program(request, program_id: int):
    try:
        program = _org_qs(request).prefetch_related('targets').get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    return _serialize_org_program(program, include_targets=True)


@router.patch('/org-programs/{program_id}', response=OrgProgramSchema)
def update_org_program(request, program_id: int, data: ProgramUpdateRequest):
    _require_admin(request)
    try:
        program = _org_qs(request).get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    for field, value in data.dict(exclude_none=True).items():
        setattr(program, field, value)
    program.save()
    return _serialize_org_program(program, include_targets=True)


@router.delete('/org-programs/{program_id}', response={204: None})
def archive_org_program(request, program_id: int):
    _require_admin(request)
    try:
        program = _org_qs(request).get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    program.status = Program.Status.ARCHIVED
    program.archived_at = timezone.now()
    program.save(update_fields=['status', 'archived_at'])
    return 204, None


def _copy_program_to_client(source: Program, client_id: int, user) -> Program:
    """Deep-copy a program (+ all its targets) to a different client."""
    dest = Program.objects.create(
        is_template=False,
        external_client_id=client_id,
        name=source.name,
        category=source.category,
        phase=source.phase,
        status=Program.Status.ACTIVE,
        treatment_area=source.treatment_area,
        tags=source.tags,
        objective=source.objective,
        instructions=source.instructions,
        display_order=source.display_order,
        created_by=user,
    )
    for t in source.targets.all():
        Target.objects.create(
            program=dest,
            name=t.name,
            measurement_type=t.measurement_type,
            prompting_template=t.prompting_template,
            mastery_template=t.mastery_template,
            sd_text=t.sd_text,
            teaching_instructions=t.teaching_instructions,
            status=t.status,
            display_order=t.display_order,
            is_visible_to_staff=t.is_visible_to_staff,
            created_by=user,
        )
    dest.refresh_from_db()
    return dest


@router.post('/org-programs/{program_id}/assign', response={201: ProgramSchema})
def assign_org_program_to_client(request, program_id: int, data: AssignOrgProgramRequest):
    """Copy a facility-level program template to a specific client."""
    _require_admin(request)
    try:
        template = _org_qs(request).prefetch_related('targets').get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    dest = _copy_program_to_client(template, data.client_id, request.user)
    return 201, {**_serialize_program(dest, include_targets=True)}


@router.post('/programs/{program_id}/copy', response={201: ProgramSchema})
def copy_program_to_client(request, program_id: int, data: AssignOrgProgramRequest):
    """Copy any client program to another client."""
    _require_supervisor(request)
    try:
        source = Program.objects.prefetch_related('targets').get(id=program_id, is_template=False)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    dest = _copy_program_to_client(source, data.client_id, request.user)
    return 201, {**_serialize_program(dest, include_targets=True)}


# ---------------------------------------------------------------------------
# Treatment Areas
# ---------------------------------------------------------------------------

@router.get('/programs/settings/treatment-areas', response=list[TreatmentAreaSchema])
def list_treatment_areas(request, include_inactive: bool = False):
    qs = TreatmentArea.objects.all()
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs)


@router.post('/programs/settings/treatment-areas', response={201: TreatmentAreaSchema})
def create_treatment_area(request, data: TreatmentAreaRequest):
    _require_admin(request)
    return 201, TreatmentArea.objects.create(created_by=request.user, **data.dict())


@router.patch('/programs/settings/treatment-areas/{pk}', response=TreatmentAreaSchema)
def update_treatment_area(request, pk: int, data: TreatmentAreaRequest):
    _require_admin(request)
    try:
        obj = TreatmentArea.objects.get(id=pk)
    except TreatmentArea.DoesNotExist:
        raise HttpError(404, 'Not found')
    for k, v in data.dict(exclude_none=True).items():
        setattr(obj, k, v)
    obj.save()
    return obj


@router.delete('/programs/settings/treatment-areas/{pk}', response={204: None})
def delete_treatment_area(request, pk: int):
    _require_admin(request)
    try:
        TreatmentArea.objects.get(id=pk).delete()
    except TreatmentArea.DoesNotExist:
        raise HttpError(404, 'Not found')
    return 204, None


# ---------------------------------------------------------------------------
# Program Tags
# ---------------------------------------------------------------------------

@router.get('/programs/settings/tags', response=list[ProgramTagSchema])
def list_program_tags(request, include_inactive: bool = False):
    qs = ProgramTag.objects.all()
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs)


@router.post('/programs/settings/tags', response={201: ProgramTagSchema})
def create_program_tag(request, data: ProgramTagRequest):
    _require_admin(request)
    return 201, ProgramTag.objects.create(created_by=request.user, **data.dict())


@router.patch('/programs/settings/tags/{pk}', response=ProgramTagSchema)
def update_program_tag(request, pk: int, data: ProgramTagRequest):
    _require_admin(request)
    try:
        obj = ProgramTag.objects.get(id=pk)
    except ProgramTag.DoesNotExist:
        raise HttpError(404, 'Not found')
    for k, v in data.dict(exclude_none=True).items():
        setattr(obj, k, v)
    obj.save()
    return obj


@router.delete('/programs/settings/tags/{pk}', response={204: None})
def delete_program_tag(request, pk: int):
    _require_admin(request)
    try:
        ProgramTag.objects.get(id=pk).delete()
    except ProgramTag.DoesNotExist:
        raise HttpError(404, 'Not found')
    return 204, None


# ---------------------------------------------------------------------------
# Target Statuses
# ---------------------------------------------------------------------------

@router.get('/programs/settings/statuses', response=list[TargetStatusSchema])
def list_target_statuses(request, include_inactive: bool = False):
    qs = TargetStatus.objects.all()
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs)

@router.post('/programs/settings/statuses', response={201: TargetStatusSchema})
def create_target_status(request, data: TargetStatusRequest):
    _require_admin(request)
    if TargetStatus.objects.filter(key=data.key).exists():
        raise HttpError(409, f'A status with key "{data.key}" already exists')
    payload = data.dict()
    if payload['is_default'] and not payload['is_active']:
        raise HttpError(400, 'An inactive status cannot be the default')
    if payload['is_default']:
        TargetStatus.objects.filter(is_default=True).update(is_default=False)
    return 201, TargetStatus.objects.create(created_by=request.user, **payload)


@router.patch('/programs/settings/statuses/{pk}', response=TargetStatusSchema)
def update_target_status(request, pk: int, data: TargetStatusUpdateRequest):
    _require_admin(request)
    try:
        obj = TargetStatus.objects.get(id=pk)
    except TargetStatus.DoesNotExist:
        raise HttpError(404, 'Not found')
    update = data.dict(exclude_none=True)
    is_default = update.get('is_default', obj.is_default)
    is_active = update.get('is_active', obj.is_active)
    if is_default and not is_active:
        raise HttpError(400, 'An inactive status cannot be the default')
    if update.get('is_default'):
        TargetStatus.objects.filter(is_default=True).exclude(id=pk).update(is_default=False)
    for k, v in update.items():
        setattr(obj, k, v)
    obj.save()
    return obj


@router.delete('/programs/settings/statuses/{pk}', response={204: None})
def delete_target_status(request, pk: int):
    _require_admin(request)
    try:
        TargetStatus.objects.get(id=pk).delete()
    except TargetStatus.DoesNotExist:
        raise HttpError(404, 'Not found')
    return 204, None


# ---------------------------------------------------------------------------
# Program Data Fields
# ---------------------------------------------------------------------------

@router.get('/programs/settings/data-fields', response=list[ProgramDataFieldSchema])
def list_data_fields(request, include_inactive: bool = False):
    qs = ProgramDataField.objects.all()
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs)


@router.post('/programs/settings/data-fields', response={201: ProgramDataFieldSchema})
def create_data_field(request, data: ProgramDataFieldRequest):
    _require_admin(request)
    return 201, ProgramDataField.objects.create(created_by=request.user, **data.dict())


@router.patch('/programs/settings/data-fields/{pk}', response=ProgramDataFieldSchema)
def update_data_field(request, pk: int, data: ProgramDataFieldRequest):
    _require_admin(request)
    try:
        obj = ProgramDataField.objects.get(id=pk)
    except ProgramDataField.DoesNotExist:
        raise HttpError(404, 'Not found')
    for k, v in data.dict(exclude_none=True).items():
        setattr(obj, k, v)
    obj.save()
    return obj


@router.delete('/programs/settings/data-fields/{pk}', response={204: None})
def delete_data_field(request, pk: int):
    _require_admin(request)
    try:
        ProgramDataField.objects.get(id=pk).delete()
    except ProgramDataField.DoesNotExist:
        raise HttpError(404, 'Not found')
    return 204, None
