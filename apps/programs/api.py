import os

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django_tenants.utils import get_public_schema_name, schema_context
from ninja import Router, File, Form
from ninja.errors import HttpError
from ninja.files import UploadedFile
from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image

from apps.accounts.api import _same_practice_q
from apps.accounts.auth import jwt_auth
from apps.accounts.permissions import require_permission
from apps.central_library.models import CentralProgram, CentralProgramFolder, KnowledgeBaseModule
from shared.uploads import validate_image_upload
from .models import (
    Program, ProgramMaterial, Target, PromptingTemplate,
    WorkflowTemplate, MaintenanceSchedule, FadingTemplate,
    Lesson, LessonProgram,
    TreatmentArea, ProgramTag, ProgramDataField, TargetStatus,
    TargetStatusChange, TargetPromptLevelChange, ProgramFolder,
    ProgramModule, ProgramSubmodule, TargetSubItem, TargetSubItemStatusChange,
    SavedTableView,
)
from .schemas import (
    ProgramSchema, ProgramListSchema, ProgramCreateRequest, ProgramUpdateRequest, ProgramMaterialSchema,
    TargetSchema, TargetCreateRequest, TargetUpdateRequest,
    BulkUpdateTargetsRequest, BulkUpdateResult, ReorderTargetsRequest,
    ReorderModulesRequest, ReorderSubmodulesRequest,
    PromptingTemplateSchema, PromptingTemplateCreateRequest, PromptingTemplateUpdateRequest,
    WorkflowTemplateSchema, WorkflowTemplateCreateRequest, WorkflowTemplateUpdateRequest,
    MaintenanceScheduleSchema, MaintenanceScheduleCreateRequest, MaintenanceScheduleUpdateRequest,
    FadingTemplateSchema, FadingTemplateCreateRequest, FadingTemplateUpdateRequest,
    LessonSchema, LessonCreateRequest, LessonUpdateRequest, AddProgramToLessonRequest,
    LessonProgramSchema,
    OrgProgramSchema, OrgProgramCreateRequest, AssignOrgProgramRequest,
    ProgramFolderSchema, ProgramFolderRequest, SetProgramFolderRequest,
    CentralProgramFolderSchema, ImportCentralFolderResult,
    TreatmentAreaSchema, TreatmentAreaRequest,
    ProgramTagSchema, ProgramTagRequest,
    ProgramDataFieldSchema, ProgramDataFieldRequest,
    TargetStatusChangeSchema, TargetPromptLevelChangeSchema,
    TargetStatusSchema, TargetStatusRequest, TargetStatusUpdateRequest,
    ProgramModuleSchema, ProgramModuleRequest, ProgramSubmoduleSchema, ProgramSubmoduleRequest,
    SavedTableViewSchema, SavedTableViewCreateRequest,
    KnowledgeBaseModuleSchema,
)

router = Router(auth=jwt_auth)


def _require_supervisor(request):
    if request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Supervisor or admin access required')


def _accessible_external_client_ids(request) -> set[int]:
    from apps.clients.api import _get_accessible_clients

    ids: set[int] = set()
    for client_id, external_id in _get_accessible_clients(request).values_list('id', 'external_id'):
        ids.add(client_id)
        if external_id:
            try:
                ids.add(int(external_id))
            except (TypeError, ValueError):
                pass
    return ids


def _program_accessible(request, program: Program) -> bool:
    """Client-bound programs are scoped by practice/assignment (see
    _accessible_external_client_ids); org-level template programs have no
    external_client_id at all, so they're scoped the same way _org_qs scopes
    them — by the creating admin's facility — instead of always failing the
    client check and 404ing (which is what happened before this branch:
    every /org-programs detail open 404'd since a template's
    external_client_id is always None)."""
    if program.is_template:
        return (
            program.created_by_id is not None
            and program.created_by.external_admin_id == request.user.external_admin_id
        )
    return program.external_client_id in _accessible_external_client_ids(request)


def _get_program_or_404(request, program_id: int) -> Program:
    try:
        program = Program.objects.select_related('created_by').get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    if not _program_accessible(request, program):
        raise HttpError(404, 'Program not found')
    return program


def _get_target_or_404(request, target_id: int) -> Target:
    try:
        target = Target.objects.select_related('program', 'program__created_by').get(id=target_id)
    except Target.DoesNotExist:
        raise HttpError(404, 'Target not found')
    if not _program_accessible(request, target.program):
        raise HttpError(404, 'Target not found')
    return target


def _assert_client_accessible(request, client_id: int) -> None:
    if client_id not in _accessible_external_client_ids(request):
        raise HttpError(404, 'Client not found')


def _get_lesson_or_404(request, lesson_id: int) -> Lesson:
    try:
        lesson = Lesson.objects.get(id=lesson_id)
    except Lesson.DoesNotExist:
        raise HttpError(404, 'Lesson not found')
    if lesson.external_client_id not in _accessible_external_client_ids(request):
        raise HttpError(404, 'Lesson not found')
    return lesson


def _require_settings_permission(request, permission: str):
    """Enforce the fine-grained settings privilege (e.g. settings_tags_create).

    Privileges are the source of truth — do not also require role=admin.
    """
    require_permission(request, permission)


def _settings_qs(model, request, *, include_org_defaults: bool = False):
    """Practice-scoped queryset for shared facility settings (treatment areas,
    tags, statuses, prompting/fading/workflow templates, maintenance schedules,
    data fields). These carry `created_by` but were previously read/written
    with no practice filter at all — Model.objects.all() — so a TPMS practice
    sharing this org's schema with another practice (see
    tenants.OrganizationTpmsAdminId) could see and edit the other practice's
    configuration. Reuses _same_practice_q exactly as APIKey/User already do,
    reached through created_by since these settings models don't carry
    external_admin_id/organization directly on the row.

    include_org_defaults=True additionally includes created_by=NULL rows —
    ONLY correct for TargetStatus, where NULL means "org-level default
    copied at org-creation time from the platform's DefaultTargetStatus
    templates" (see apps.tenants.services.copy_default_target_statuses_to_org),
    which belongs to every practice in the org's schema. Every other
    settings model's NULL created_by rows are legacy data from before this
    practice filter existed — ownership is simply unknown for those, NOT
    "belongs to everyone" — so defaulting this to True broke practice
    isolation across every settings tab, not just statuses. Leave it False
    unless the model is TargetStatus."""
    qs = model.objects.filter(_same_practice_q(request.user, 'created_by__'))
    if include_org_defaults:
        qs = model.objects.filter(_same_practice_q(request.user, 'created_by__') | models.Q(created_by__isnull=True))
    return qs


def _validate_treatment_area_and_tags(request, treatment_area: str | None, tags: list[str] | None) -> None:
    """treatment_area and tags are free text/JSON on Program, not FKs — but
    they're meant to be drawn from the org's configured TreatmentArea/ProgramTag
    lists (see settings page), so a value that matches neither is almost
    always a typo or a stale client, not an intentional new value."""
    if treatment_area and not _settings_qs(TreatmentArea, request).filter(name=treatment_area).exists():
        raise HttpError(400, f'Unknown treatment_area: {treatment_area}')
    if tags:
        valid = set(_settings_qs(ProgramTag, request).filter(name__in=tags).values_list('name', flat=True))
        invalid = [t for t in tags if t not in valid]
        if invalid:
            raise HttpError(400, f'Unknown tag(s): {", ".join(invalid)}')


def _check_unique_name(model, request, name: str, *, exclude_id: int | None = None) -> None:
    """Pre-check for the (practice, name) uniqueness constraint on settings
    entities — gives a clean 409 instead of the IntegrityError a same-name
    insert/update would otherwise raise. Scoped per-practice (see
    _settings_qs) so two practices sharing one org's schema can each have
    their own "Communication" treatment area, "Default" workflow, etc."""
    qs = _settings_qs(model, request).filter(name=name)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    if qs.exists():
        raise HttpError(409, f'{model.__name__} named "{name}" already exists')


def _serialize_saved_view(view: SavedTableView, request) -> dict:
    return {
        'id': view.id,
        'table_key': view.table_key,
        'name': view.name,
        'config': view.config,
        'visibility': view.visibility,
        'roles': view.roles,
        'display_order': view.display_order,
        'created_by_id': view.created_by_id,
        'is_mine': view.created_by_id == request.user.id,
        'created_at': view.created_at,
        'updated_at': view.updated_at,
    }


def _visible_saved_views_qs(request, table_key: str):
    """Views a user may see for a given table: their own (any visibility),
    plus anyone's 'everyone' views, plus 'roles' views naming their role."""
    return _settings_qs(SavedTableView, request).filter(table_key=table_key).filter(
        models.Q(created_by=request.user)
        | models.Q(visibility=SavedTableView.Visibility.EVERYONE)
        | models.Q(visibility=SavedTableView.Visibility.ROLES, roles__contains=[request.user.role])
    )


# Keep these static /programs/* collection routes above /programs/{program_id}.
# Some routing layers match in declaration order, and a dynamic program_id route
# can otherwise shadow /programs/saved-views and surface as a 405 on POST.
@router.get('/programs/saved-views', response=list[SavedTableViewSchema])
def list_saved_views(request, table_key: str):
    qs = _visible_saved_views_qs(request, table_key)
    return [_serialize_saved_view(v, request) for v in qs]


@router.post('/programs/saved-views', response={201: SavedTableViewSchema})
def create_saved_view(request, data: SavedTableViewCreateRequest):
    if data.visibility not in SavedTableView.Visibility.values:
        raise HttpError(400, f'Invalid visibility: {data.visibility}')
    view = SavedTableView.objects.create(
        table_key=data.table_key,
        name=data.name,
        config=data.config,
        visibility=data.visibility,
        roles=data.roles,
        display_order=data.display_order,
        created_by=request.user,
    )
    return 201, _serialize_saved_view(view, request)


@router.delete('/programs/saved-views/{view_id}', response={204: None})
def delete_saved_view(request, view_id: int):
    try:
        view = _settings_qs(SavedTableView, request).get(id=view_id)
    except SavedTableView.DoesNotExist:
        raise HttpError(404, 'Saved view not found')
    if view.created_by_id != request.user.id and request.user.role not in ('admin', 'supervisor'):
        raise HttpError(403, 'Only the creator or a supervisor/admin can delete this saved view')
    view.delete()
    return 204, None


def _serialize_knowledge_base_module(module: KnowledgeBaseModule) -> dict:
    return {
        'id': module.id,
        'slug': module.slug,
        'title': module.title,
        'path': module.path,
        'icon': module.icon,
        'overview': module.overview,
        'audience': module.audience,
        'display_order': module.display_order,
        'updated_at': module.updated_at,
        'topics': [
            {
                'id': topic.id,
                'title': topic.title,
                'summary': topic.summary,
                'items': topic.items,
                'display_order': topic.display_order,
            }
            for topic in module.topics.all()
            if topic.is_active
        ],
    }


@router.get('/knowledge-base/modules', response=list[KnowledgeBaseModuleSchema])
def list_knowledge_base_modules(request):
    with schema_context(get_public_schema_name()):
        modules = (
            KnowledgeBaseModule.objects
            .filter(is_active=True)
            .prefetch_related('topics')
            .order_by('display_order', 'title')
        )
        return [_serialize_knowledge_base_module(module) for module in modules]


PROGRAM_MATERIAL_IMAGE_TYPES = {'image/jpeg', 'image/png'}
PROGRAM_MATERIAL_VIDEO_TYPES = {'video/mp4', 'video/quicktime'}
PROGRAM_MATERIAL_DOCUMENT_TYPES = {
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}
MAX_PROGRAM_MATERIAL_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PROGRAM_MATERIAL_VIDEO_BYTES = 100 * 1024 * 1024
MAX_PROGRAM_MATERIAL_DOCUMENT_BYTES = 10 * 1024 * 1024


def _program_material_type_for(file: UploadedFile) -> str:
    content_type = file.content_type or ''
    if content_type in PROGRAM_MATERIAL_IMAGE_TYPES:
        if file.size > MAX_PROGRAM_MATERIAL_IMAGE_BYTES:
            raise HttpError(400, 'Images must be under 5MB')
        validate_image_upload(file, max_bytes=MAX_PROGRAM_MATERIAL_IMAGE_BYTES)
        return ProgramMaterial.MaterialType.IMAGE
    if content_type in PROGRAM_MATERIAL_VIDEO_TYPES:
        if file.size > MAX_PROGRAM_MATERIAL_VIDEO_BYTES:
            raise HttpError(400, 'Videos must be under 100MB')
        return ProgramMaterial.MaterialType.VIDEO
    if content_type in PROGRAM_MATERIAL_DOCUMENT_TYPES:
        if file.size > MAX_PROGRAM_MATERIAL_DOCUMENT_BYTES:
            raise HttpError(400, 'Documents must be under 10MB')
        return ProgramMaterial.MaterialType.DOCUMENT
    allowed = sorted(PROGRAM_MATERIAL_IMAGE_TYPES | PROGRAM_MATERIAL_VIDEO_TYPES | PROGRAM_MATERIAL_DOCUMENT_TYPES)
    raise HttpError(400, f'Learning material must be one of: {", ".join(allowed)}')


def _serialize_program_material(material: ProgramMaterial, request) -> ProgramMaterialSchema:
    return ProgramMaterialSchema(
        id=material.id,
        program_id=material.program_id,
        title=material.title,
        material_type=material.material_type,
        file_url=request.build_absolute_uri(material.file.url),
        content_type=material.content_type,
        file_size=material.file_size,
        uploaded_by=material.created_by.email if material.created_by_id else None,
        created_at=material.created_at,
    )


def _serialize_program(program: Program, request=None, include_targets: bool = False) -> dict:
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
        'prompting_template_id': program.prompting_template_id,
        'workflow_template_id': program.workflow_template_id,
        'maintenance_schedule_id': program.maintenance_schedule_id,
        'fading_template_id': program.fading_template_id,
        'image_url': _optimized_program_image_url(request, program.image) if request is not None else None,
        'display_order': program.display_order,
        'archived_at': program.archived_at,
        'created_at': program.created_at,
        'updated_at': program.updated_at,
    }
    if include_targets:
        data['targets'] = list(program.targets.all().values(
            'id', 'name', 'status', 'display_order', 'is_visible_to_staff',
            'module_id', 'submodule_id',
        ))
        data['materials'] = [
            _serialize_program_material(material, request)
            for material in program.materials.select_related('created_by')
        ] if request is not None else []
    return data


def _optimized_program_image_url(request, image_field) -> str | None:
    if not image_field:
        return None

    try:
        source_path = image_field.path
        source_url = image_field.url
    except (NotImplementedError, ValueError):
        return request.build_absolute_uri(image_field.url)

    optimized_path = f'{source_path}.card.webp'
    optimized_url = f'{source_url}.card.webp'

    try:
        source_mtime = os.path.getmtime(source_path)
        optimized_is_current = (
            os.path.exists(optimized_path)
            and os.path.getmtime(optimized_path) >= source_mtime
        )
        if not optimized_is_current:
            with Image.open(source_path) as img:
                img = img.convert('RGB')
                img.thumbnail((1200, 800), Image.Resampling.LANCZOS)
                img.save(optimized_path, 'WEBP', quality=82, method=6)
    except OSError:
        return request.build_absolute_uri(source_url)

    return request.build_absolute_uri(optimized_url)


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------

@router.get('/programs', response=list[ProgramListSchema])
def list_programs(request, client_id: int, category: str | None = None, status: str | None = None):
    qs = Program.objects.filter(
        external_client_id=client_id,
        external_client_id__in=_accessible_external_client_ids(request),
    ).exclude(status='archived')
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
            **_serialize_program(p, request),
            'target_count': len(targets),
            'target_status_counts': status_counts,
        })
    return result


@router.post('/programs', response={201: ProgramSchema})
def create_program(request, data: ProgramCreateRequest):
    _require_supervisor(request)
    _assert_client_accessible(request, data.client_id)
    _validate_treatment_area_and_tags(request, data.treatment_area, data.tags)
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
        prompting_template_id=data.prompting_template_id,
        workflow_template_id=data.workflow_template_id,
        maintenance_schedule_id=data.maintenance_schedule_id,
        fading_template_id=data.fading_template_id,
        display_order=data.display_order,
        created_by=request.user,
    )
    return 201, {**_serialize_program(program, request), 'targets': []}


@router.get('/programs/{program_id}', response=ProgramSchema)
def get_program(request, program_id: int):
    program = _get_program_or_404(request, program_id)
    return {**_serialize_program(program, request, include_targets=True)}


@router.patch('/programs/{program_id}', response=ProgramSchema)
def update_program(request, program_id: int, data: ProgramUpdateRequest):
    _require_supervisor(request)
    program = _get_program_or_404(request, program_id)
    updates = data.dict(exclude_none=True)
    if 'treatment_area' in updates or 'tags' in updates:
        _validate_treatment_area_and_tags(
            request,
            updates.get('treatment_area', program.treatment_area),
            updates.get('tags', program.tags),
        )
    for field, value in updates.items():
        setattr(program, field, value)
    program.save()
    if 'workflow_template_id' in updates:
        program.targets.update(workflow_template_id=program.workflow_template_id)
    if 'prompting_template_id' in updates:
        program.targets.update(
            prompting_template_id=program.prompting_template_id,
            current_prompt_level_index=0,
        )
    if 'fading_template_id' in updates:
        program.targets.update(fading_template_id=program.fading_template_id)
    return {**_serialize_program(program, request, include_targets=True)}


@router.post('/programs/{program_id}/image', response=ProgramSchema)
def upload_program_image(request, program_id: int, file: UploadedFile = File(...)):
    _require_supervisor(request)
    program = _get_program_or_404(request, program_id)
    validate_image_upload(file)
    program.image = file
    program.save(update_fields=['image'])
    return {**_serialize_program(program, request, include_targets=True)}


@router.get('/programs/{program_id}/materials', response=list[ProgramMaterialSchema])
def list_program_materials(request, program_id: int):
    program = _get_program_or_404(request, program_id)
    return [
        _serialize_program_material(material, request)
        for material in program.materials.select_related('created_by')
    ]


@router.post('/programs/{program_id}/materials', response={201: ProgramMaterialSchema})
def upload_program_material(
    request,
    program_id: int,
    file: UploadedFile = File(...),
    title: str = Form(''),
):
    _require_supervisor(request)
    program = _get_program_or_404(request, program_id)
    material_type = _program_material_type_for(file)
    material = ProgramMaterial.objects.create(
        program=program,
        title=(title or file.name).strip()[:255],
        material_type=material_type,
        file=file,
        content_type=file.content_type or '',
        file_size=file.size,
        created_by=request.user,
    )
    return 201, _serialize_program_material(material, request)


@router.delete('/program-materials/{material_id}', response={204: None})
def delete_program_material(request, material_id: int):
    _require_supervisor(request)
    try:
        material = ProgramMaterial.objects.select_related('program').get(id=material_id)
    except ProgramMaterial.DoesNotExist:
        raise HttpError(404, 'Learning material not found')
    _get_program_or_404(request, material.program_id)
    material.delete()
    return 204, None


@router.delete('/programs/{program_id}', response={204: None})
def archive_program(request, program_id: int):
    _require_supervisor(request)
    program = _get_program_or_404(request, program_id)
    program.status = Program.Status.ARCHIVED
    program.archived_at = timezone.now()
    program.save(update_fields=['status', 'archived_at'])
    return 204, None


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

_SUB_ITEM_MEASUREMENT_TYPES = {
    Target.MeasurementType.TASK_ANALYSIS,
    Target.MeasurementType.SET_OF_TARGETS,
    Target.MeasurementType.SHAPING,
}


def _require_sub_items_if_needed(measurement_type: str, sub_items: list) -> None:
    if measurement_type in _SUB_ITEM_MEASUREMENT_TYPES and not sub_items:
        raise HttpError(400, f'{measurement_type} targets require at least one sub_item')


def _default_sub_item_status(target: Target, index: int, total: int) -> str:
    if target.sub_item_progression == Target.SubItemProgression.TOTAL_TASK:
        return TargetSubItem.Status.ACQUISITION
    if target.sub_item_progression == Target.SubItemProgression.BACKWARD:
        return TargetSubItem.Status.ACQUISITION if index == total - 1 else TargetSubItem.Status.WAITING
    return TargetSubItem.Status.ACQUISITION if index == 0 else TargetSubItem.Status.WAITING


def _sync_target_sub_items(target: Target, items: list[dict], user=None) -> None:
    existing = {item.key: item for item in target.child_items.all()}
    seen: set[str] = set()
    normalized: list[dict] = []
    total = len(items)

    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        key = str(raw.get('key') or f'item_{idx + 1}')[:100]
        label = str(raw.get('label') or key)[:200]
        status = str(raw.get('status') or '')
        if status and not TargetSubItem.Status.values.__contains__(status):
            raise HttpError(400, f'Invalid sub item status: {status}')
        status = status or _default_sub_item_status(target, idx, total)
        seen.add(key)

        child = existing.get(key)
        if child:
            old_status = child.status
            child.label = label
            child.status = status
            child.display_order = idx
            child.save(update_fields=['label', 'status', 'display_order', 'updated_at'])
            if old_status != status:
                TargetSubItemStatusChange.objects.create(
                    sub_item=child,
                    from_status=old_status,
                    to_status=status,
                    trigger=TargetSubItemStatusChange.Trigger.MANUAL,
                    created_by=user,
                )
        else:
            child = TargetSubItem.objects.create(
                target=target,
                key=key,
                label=label,
                status=status,
                display_order=idx,
                created_by=user,
            )
            TargetSubItemStatusChange.objects.create(
                sub_item=child,
                from_status='',
                to_status=status,
                trigger=TargetSubItemStatusChange.Trigger.MANUAL,
                created_by=user,
            )
        normalized.append({'key': key, 'label': label, 'status': status})

    target.child_items.exclude(key__in=seen).delete()
    if target.sub_items != normalized:
        target.sub_items = normalized
        target.save(update_fields=['sub_items', 'updated_at'])


def _refresh_target_sub_items_json(target: Target) -> None:
    children = list(target.child_items.all().order_by('display_order', 'id'))
    if not children:
        return
    serialized = [
        {'key': item.key, 'label': item.label, 'status': item.status}
        for item in children
    ]
    if target.sub_items != serialized:
        target.sub_items = serialized
        target.save(update_fields=['sub_items', 'updated_at'])


def _validate_target_status(request, status: str) -> None:
    if status and not _settings_qs(TargetStatus, request, include_org_defaults=True).filter(key=status).exists():
        raise HttpError(400, f'Invalid status: {status}')


@router.get('/programs/{program_id}/targets', response=list[TargetSchema])
def list_targets(request, program_id: int, staff_view: bool = False):
    program = _get_program_or_404(request, program_id)
    qs = program.targets.all()
    if staff_view:
        qs = qs.visible_to_staff()
    return list(qs)


@router.post('/programs/{program_id}/targets', response={201: TargetSchema})
def create_target(request, program_id: int, data: TargetCreateRequest):
    _require_supervisor(request)
    program = _get_program_or_404(request, program_id)
    target_data = data.dict()
    if program.prompting_template_id and not target_data.get('prompting_template_id'):
        target_data['prompting_template_id'] = program.prompting_template_id
    if program.workflow_template_id and not target_data.get('workflow_template_id'):
        target_data['workflow_template_id'] = program.workflow_template_id
    if program.fading_template_id and not target_data.get('fading_template_id'):
        target_data['fading_template_id'] = program.fading_template_id
    if target_data.get('status'):
        _validate_target_status(request, target_data['status'])
    else:
        default_status = _settings_qs(TargetStatus, request, include_org_defaults=True).filter(is_default=True).first()
        target_data['status'] = default_status.key if default_status else 'waiting'
    _require_sub_items_if_needed(target_data['measurement_type'], target_data['sub_items'])
    target = Target.objects.create(
        program=program,
        created_by=request.user,
        **target_data,
    )
    if target.measurement_type in _SUB_ITEM_MEASUREMENT_TYPES:
        _sync_target_sub_items(target, target.sub_items, request.user)
    return 201, target


@router.get('/targets/{target_id}', response=TargetSchema)
def get_target(request, target_id: int):
    return _get_target_or_404(request, target_id)


@router.patch('/targets/{target_id}', response=TargetSchema)
def update_target(request, target_id: int, data: TargetUpdateRequest):
    _require_supervisor(request)
    target = _get_target_or_404(request, target_id)
    updates = data.dict(exclude_none=True)
    for field, value in updates.items():
        setattr(target, field, value)
    if 'prompting_template_id' in updates:
        # Swapping the level hierarchy invalidates whatever level index the
        # target was previously faded to — reset to the most-intrusive level.
        target.current_prompt_level_index = 0
    if 'status' in updates:
        _validate_target_status(request, updates['status'])
    _require_sub_items_if_needed(target.measurement_type, target.sub_items)
    target.save()
    if target.measurement_type in _SUB_ITEM_MEASUREMENT_TYPES and (
        'sub_items' in updates or 'sub_item_progression' in updates
    ):
        _sync_target_sub_items(target, target.sub_items, request.user)
    elif target.measurement_type in _SUB_ITEM_MEASUREMENT_TYPES:
        _refresh_target_sub_items_json(target)
    return target


@router.delete('/targets/{target_id}', response={204: None})
def delete_target(request, target_id: int):
    _require_supervisor(request)
    target = _get_target_or_404(request, target_id)
    target.delete()
    return 204, None


@router.get('/targets/{target_id}/history', response=list[TargetStatusChangeSchema])
def target_history(request, target_id: int):
    _get_target_or_404(request, target_id)
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


@router.get('/targets/{target_id}/prompt-level-history', response=list[TargetPromptLevelChangeSchema])
def target_prompt_level_history(request, target_id: int):
    _get_target_or_404(request, target_id)
    qs = (
        TargetPromptLevelChange.objects
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
        result.append(TargetPromptLevelChangeSchema(
            id=entry.id,
            from_level_index=entry.from_level_index,
            to_level_index=entry.to_level_index,
            from_level_label=entry.from_level_label,
            to_level_label=entry.to_level_label,
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
    'workflow_template_id': WorkflowTemplate,
    'maintenance_schedule_id': MaintenanceSchedule,
    'fading_template_id': FadingTemplate,
}


@router.post('/programs/{program_id}/targets/bulk-update', response=BulkUpdateResult)
def bulk_update_targets(request, program_id: int, data: BulkUpdateTargetsRequest):
    """Update specific fields across multiple targets without touching unspecified fields."""
    _require_supervisor(request)
    _get_program_or_404(request, program_id)
    updates = data.dict(exclude={'target_ids'}, exclude_none=True)
    if not updates:
        raise HttpError(400, 'No fields to update were provided')

    for field_name, model in _BULK_UPDATE_FK_MODELS.items():
        fk_id = updates.get(field_name)
        if fk_id is not None and not model.objects.filter(id=fk_id).exists():
            raise HttpError(400, f'Invalid {field_name}: {fk_id}')

    if updates.get('status'):
        _validate_target_status(request, updates['status'])

    if updates.get('measurement_type') in _SUB_ITEM_MEASUREMENT_TYPES:
        missing_sub_items = Target.objects.filter(
            id__in=data.target_ids, program_id=program_id, sub_items=[],
        ).exists()
        if missing_sub_items:
            raise HttpError(400, f'{updates["measurement_type"]} requires targets to already have sub_items')

    if 'prompting_template_id' in updates:
        updates['current_prompt_level_index'] = 0
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
    _get_program_or_404(request, program_id)
    for order, target_id in enumerate(data.ordered_ids):
        Target.objects.filter(id=target_id, program_id=program_id).update(display_order=order)
    return 200, None


# ---------------------------------------------------------------------------
# Prompting templates
# ---------------------------------------------------------------------------

@router.get('/programs/templates/prompting', response=list[PromptingTemplateSchema])
def list_prompting_templates(request):
    return list(_settings_qs(PromptingTemplate, request))


@router.post('/programs/templates/prompting', response={201: PromptingTemplateSchema})
def create_prompting_template(request, data: PromptingTemplateCreateRequest):
    _require_settings_permission(request, 'settings_prompting_templates_create')
    _check_unique_name(PromptingTemplate, request, data.name)
    template = PromptingTemplate.objects.create(created_by=request.user, **data.dict())
    return 201, template


@router.patch('/programs/templates/prompting/{template_id}', response=PromptingTemplateSchema)
def update_prompting_template(request, template_id: int, data: PromptingTemplateUpdateRequest):
    _require_settings_permission(request, 'settings_prompting_templates_edit')
    try:
        template = _settings_qs(PromptingTemplate, request).get(id=template_id)
    except PromptingTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')
    if data.name:
        _check_unique_name(PromptingTemplate, request, data.name, exclude_id=template_id)
    for field, value in data.dict(exclude_none=True).items():
        setattr(template, field, value)
    template.save()
    return template


@router.delete('/programs/templates/prompting/{template_id}', response={204: None})
def delete_prompting_template(request, template_id: int):
    _require_settings_permission(request, 'settings_prompting_templates_delete')
    try:
        _settings_qs(PromptingTemplate, request).get(id=template_id).delete()
    except PromptingTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')
    return 204, None


# ---------------------------------------------------------------------------
# Fading templates
# ---------------------------------------------------------------------------

@router.get('/programs/templates/fading', response=list[FadingTemplateSchema])
def list_fading_templates(request):
    return list(_settings_qs(FadingTemplate, request))


@router.post('/programs/templates/fading', response={201: FadingTemplateSchema})
def create_fading_template(request, data: FadingTemplateCreateRequest):
    _require_settings_permission(request, 'settings_fading_templates_create')
    _check_unique_name(FadingTemplate, request, data.name)
    template = FadingTemplate.objects.create(created_by=request.user, **data.dict())
    return 201, template


@router.patch('/programs/templates/fading/{template_id}', response=FadingTemplateSchema)
def update_fading_template(request, template_id: int, data: FadingTemplateUpdateRequest):
    _require_settings_permission(request, 'settings_fading_templates_edit')
    try:
        template = _settings_qs(FadingTemplate, request).get(id=template_id)
    except FadingTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')
    if data.name:
        _check_unique_name(FadingTemplate, request, data.name, exclude_id=template_id)
    for field, value in data.dict(exclude_none=True).items():
        setattr(template, field, value)
    template.save()
    return template


@router.delete('/programs/templates/fading/{template_id}', response={204: None})
def delete_fading_template(request, template_id: int):
    _require_settings_permission(request, 'settings_fading_templates_delete')
    try:
        _settings_qs(FadingTemplate, request).get(id=template_id).delete()
    except FadingTemplate.DoesNotExist:
        raise HttpError(404, 'Template not found')
    return 204, None


# ---------------------------------------------------------------------------
# Workflow templates
# ---------------------------------------------------------------------------

@router.get('/programs/templates/workflow', response=list[WorkflowTemplateSchema])
def list_workflow_templates(request, include_inactive: bool = False):
    qs = _settings_qs(WorkflowTemplate, request)
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs)


@router.post('/programs/templates/workflow', response={201: WorkflowTemplateSchema})
def create_workflow_template(request, data: WorkflowTemplateCreateRequest):
    _require_settings_permission(request, 'settings_workflows_create')
    _check_unique_name(WorkflowTemplate, request, data.name)
    template = WorkflowTemplate.objects.create(created_by=request.user, **data.dict())
    return 201, template


@router.get('/programs/templates/workflow/{template_id}', response=WorkflowTemplateSchema)
def get_workflow_template(request, template_id: int):
    try:
        return _settings_qs(WorkflowTemplate, request).get(id=template_id)
    except WorkflowTemplate.DoesNotExist:
        raise HttpError(404, 'Workflow template not found')


@router.patch('/programs/templates/workflow/{template_id}', response=WorkflowTemplateSchema)
def update_workflow_template(request, template_id: int, data: WorkflowTemplateUpdateRequest):
    _require_settings_permission(request, 'settings_workflows_edit')
    try:
        template = _settings_qs(WorkflowTemplate, request).get(id=template_id)
    except WorkflowTemplate.DoesNotExist:
        raise HttpError(404, 'Workflow template not found')
    if data.name:
        _check_unique_name(WorkflowTemplate, request, data.name, exclude_id=template_id)
    for field, value in data.dict(exclude_none=True).items():
        setattr(template, field, value)
    template.save()
    return template


@router.delete('/programs/templates/workflow/{template_id}', response={204: None})
def delete_workflow_template(request, template_id: int):
    _require_settings_permission(request, 'settings_workflows_delete')
    try:
        _settings_qs(WorkflowTemplate, request).get(id=template_id).delete()
    except WorkflowTemplate.DoesNotExist:
        raise HttpError(404, 'Workflow template not found')
    return 204, None


# ---------------------------------------------------------------------------
# Maintenance schedules
# ---------------------------------------------------------------------------

@router.get('/programs/templates/maintenance', response=list[MaintenanceScheduleSchema])
def list_maintenance_schedules(request):
    return list(_settings_qs(MaintenanceSchedule, request))


@router.post('/programs/templates/maintenance', response={201: MaintenanceScheduleSchema})
def create_maintenance_schedule(request, data: MaintenanceScheduleCreateRequest):
    _require_settings_permission(request, 'settings_maintenance_schedules_create')
    _check_unique_name(MaintenanceSchedule, request, data.name)
    schedule = MaintenanceSchedule.objects.create(created_by=request.user, **data.dict())
    return 201, schedule


@router.get('/programs/templates/maintenance/{schedule_id}', response=MaintenanceScheduleSchema)
def get_maintenance_schedule(request, schedule_id: int):
    try:
        return _settings_qs(MaintenanceSchedule, request).get(id=schedule_id)
    except MaintenanceSchedule.DoesNotExist:
        raise HttpError(404, 'Maintenance schedule not found')


@router.patch('/programs/templates/maintenance/{schedule_id}', response=MaintenanceScheduleSchema)
def update_maintenance_schedule(request, schedule_id: int, data: MaintenanceScheduleUpdateRequest):
    _require_settings_permission(request, 'settings_maintenance_schedules_edit')
    try:
        schedule = _settings_qs(MaintenanceSchedule, request).get(id=schedule_id)
    except MaintenanceSchedule.DoesNotExist:
        raise HttpError(404, 'Maintenance schedule not found')
    if data.name:
        _check_unique_name(MaintenanceSchedule, request, data.name, exclude_id=schedule_id)
    for field, value in data.dict(exclude_none=True).items():
        setattr(schedule, field, value)
    schedule.save()
    return schedule


@router.delete('/programs/templates/maintenance/{schedule_id}', response={204: None})
def delete_maintenance_schedule(request, schedule_id: int):
    _require_settings_permission(request, 'settings_maintenance_schedules_delete')
    try:
        _settings_qs(MaintenanceSchedule, request).get(id=schedule_id).delete()
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
    lessons = Lesson.objects.filter(
        external_client_id=client_id,
        external_client_id__in=_accessible_external_client_ids(request),
        is_active=True,
    )
    return [_serialize_lesson(l) for l in lessons]


@router.post('/lessons', response={201: LessonSchema})
def create_lesson(request, data: LessonCreateRequest):
    _require_supervisor(request)
    _assert_client_accessible(request, data.client_id)
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
    return _serialize_lesson(_get_lesson_or_404(request, lesson_id))


@router.patch('/lessons/{lesson_id}', response=LessonSchema)
def update_lesson(request, lesson_id: int, data: LessonUpdateRequest):
    _require_supervisor(request)
    lesson = _get_lesson_or_404(request, lesson_id)
    for field, value in data.dict(exclude_none=True).items():
        setattr(lesson, field, value)
    lesson.save()
    return _serialize_lesson(lesson)


@router.post('/lessons/{lesson_id}/programs', response={201: LessonProgramSchema})
def add_program_to_lesson(request, lesson_id: int, data: AddProgramToLessonRequest):
    _require_supervisor(request)
    lesson = _get_lesson_or_404(request, lesson_id)
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
    _get_lesson_or_404(request, lesson_id)
    LessonProgram.objects.filter(lesson_id=lesson_id, program_id=program_id).delete()
    return 204, None


# ---------------------------------------------------------------------------
# Org-level program library (facility-wide templates)
# ---------------------------------------------------------------------------

def _serialize_org_program(program: Program, request, include_targets: bool = False) -> dict:
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
        'prompting_template_id': program.prompting_template_id,
        'workflow_template_id': program.workflow_template_id,
        'maintenance_schedule_id': program.maintenance_schedule_id,
        'fading_template_id': program.fading_template_id,
        'folder_id': program.folder_id,
        'image_url': _optimized_program_image_url(request, program.image),
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
def list_org_programs(
    request, category: str | None = None, status: str | None = None,
    folder_id: int | None = None, unfiled: bool = False,
):
    # Readable by anyone authenticated — used by Program Library and the
    # client "From Library" picker. Mutations are gated separately.
    qs = _org_qs(request).exclude(status='archived')
    if category:
        qs = qs.filter(category=category)
    if status:
        qs = qs.filter(status=status)
    if folder_id is not None:
        qs = qs.filter(folder_id=folder_id)
    elif unfiled:
        qs = qs.filter(folder_id__isnull=True)
    return [_serialize_org_program(p, request) for p in qs.prefetch_related('targets')]


@router.post('/org-programs', response={201: OrgProgramSchema})
def create_org_program(request, data: OrgProgramCreateRequest):
    require_permission(request, 'org_programs_create')
    _validate_treatment_area_and_tags(request, data.treatment_area, data.tags)
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
        prompting_template_id=data.prompting_template_id,
        workflow_template_id=data.workflow_template_id,
        fading_template_id=data.fading_template_id,
        display_order=data.display_order,
        created_by=request.user,
    )
    return 201, _serialize_org_program(program, request, include_targets=True)


# ---------------------------------------------------------------------------
# Org program folders
#
# Registered before the /org-programs/{program_id} routes below — Django's
# resolver matches URL patterns in registration order, and an untyped path
# param matches any segment, so 'folders' would otherwise be swallowed by
# {program_id} and 405 (method not allowed on that operation) instead of
# reaching these handlers.
# ---------------------------------------------------------------------------

def _serialize_program_folder(folder: ProgramFolder) -> dict:
    return {
        'id': folder.id,
        'name': folder.name,
        'display_order': folder.display_order,
        'program_count': folder.programs.exclude(status='archived').count(),
        'created_at': folder.created_at,
        'updated_at': folder.updated_at,
    }


@router.get('/org-programs/folders', response=list[ProgramFolderSchema])
def list_program_folders(request):
    return [_serialize_program_folder(f) for f in _settings_qs(ProgramFolder, request)]


@router.post('/org-programs/folders', response={201: ProgramFolderSchema})
def create_program_folder(request, data: ProgramFolderRequest):
    require_permission(request, 'org_programs_create')
    _check_unique_name(ProgramFolder, request, data.name)
    folder = ProgramFolder.objects.create(created_by=request.user, **data.dict())
    return 201, _serialize_program_folder(folder)


@router.patch('/org-programs/folders/{folder_id}', response=ProgramFolderSchema)
def update_program_folder(request, folder_id: int, data: ProgramFolderRequest):
    require_permission(request, 'org_programs_edit')
    try:
        folder = _settings_qs(ProgramFolder, request).get(id=folder_id)
    except ProgramFolder.DoesNotExist:
        raise HttpError(404, 'Folder not found')
    _check_unique_name(ProgramFolder, request, data.name, exclude_id=folder_id)
    for k, v in data.dict().items():
        setattr(folder, k, v)
    folder.save()
    return _serialize_program_folder(folder)


@router.delete('/org-programs/folders/{folder_id}', response={204: None})
def delete_program_folder(request, folder_id: int):
    require_permission(request, 'org_programs_delete')
    try:
        folder = _settings_qs(ProgramFolder, request).get(id=folder_id)
    except ProgramFolder.DoesNotExist:
        raise HttpError(404, 'Folder not found')
    # Programs inside fall back to unfiled (Program.folder is on_delete=SET_NULL)
    folder.delete()
    return 204, None


@router.post('/org-programs/{program_id}/folder', response=OrgProgramSchema)
def set_org_program_folder(request, program_id: int, data: SetProgramFolderRequest):
    require_permission(request, 'org_programs_edit')
    try:
        program = _org_qs(request).get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    if data.folder_id is not None and not ProgramFolder.objects.filter(id=data.folder_id).exists():
        raise HttpError(404, 'Folder not found')
    program.folder_id = data.folder_id
    program.save(update_fields=['folder'])
    return _serialize_org_program(program, request, include_targets=True)


@router.get('/org-programs/{program_id}', response=OrgProgramSchema)
def get_org_program(request, program_id: int):
    try:
        program = _org_qs(request).prefetch_related('targets').get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    return _serialize_org_program(program, request, include_targets=True)


@router.patch('/org-programs/{program_id}', response=OrgProgramSchema)
def update_org_program(request, program_id: int, data: ProgramUpdateRequest):
    require_permission(request, 'org_programs_edit')
    try:
        program = _org_qs(request).get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    updates = data.dict(exclude_none=True)
    if 'treatment_area' in updates or 'tags' in updates:
        _validate_treatment_area_and_tags(
            request,
            updates.get('treatment_area', program.treatment_area),
            updates.get('tags', program.tags),
        )
    for field, value in updates.items():
        setattr(program, field, value)
    program.save()
    if 'prompting_template_id' in updates:
        program.targets.update(
            prompting_template_id=program.prompting_template_id,
            current_prompt_level_index=0,
        )
    if 'workflow_template_id' in updates:
        program.targets.update(workflow_template_id=program.workflow_template_id)
    if 'fading_template_id' in updates:
        program.targets.update(fading_template_id=program.fading_template_id)
    return _serialize_org_program(program, request, include_targets=True)


@router.post('/org-programs/{program_id}/image', response=OrgProgramSchema)
def upload_org_program_image(request, program_id: int, file: UploadedFile = File(...)):
    require_permission(request, 'org_programs_edit')
    try:
        program = _org_qs(request).get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    validate_image_upload(file)
    program.image = file
    program.save(update_fields=['image'])
    return _serialize_org_program(program, request, include_targets=True)


@router.delete('/org-programs/{program_id}', response={204: None})
def archive_org_program(request, program_id: int):
    require_permission(request, 'org_programs_delete')
    try:
        program = _org_qs(request).get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    program.status = Program.Status.ARCHIVED
    program.archived_at = timezone.now()
    program.save(update_fields=['status', 'archived_at'])
    return 204, None


def _copy_image(source_image, dest) -> None:
    """Duplicates an image file's bytes onto `dest.image` (own storage key,
    own upload path) rather than pointing at the source's file — so later
    replacing/deleting one copy never affects the other."""
    if not source_image:
        return
    filename = source_image.name.rsplit('/', 1)[-1]
    dest.image.save(filename, ContentFile(source_image.read()), save=True)


def _copy_materials(source: Program, dest: Program, user) -> None:
    for material in source.materials.all():
        filename = material.file.name.rsplit('/', 1)[-1]
        copied = ProgramMaterial.objects.create(
            program=dest,
            title=material.title,
            material_type=material.material_type,
            content_type=material.content_type,
            file_size=material.file_size,
            created_by=user,
        )
        copied.file.save(filename, ContentFile(material.file.read()), save=True)


def _copy_program_to_client(source: Program, client_id: int, user) -> Program:
    """Deep-copy a program (+ modules, submodules, targets) to a different client."""
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
        prompting_template=source.prompting_template,
        workflow_template=source.workflow_template,
        maintenance_schedule=source.maintenance_schedule,
        fading_template=source.fading_template,
        display_order=source.display_order,
        created_by=user,
    )
    _copy_image(source.image, dest)
    _copy_materials(source, dest, user)
    # Clone modules + submodules, keeping a mapping from source id → dest instance
    module_map: dict[int, ProgramModule] = {}
    submodule_map: dict[int, ProgramSubmodule] = {}
    for mod in source.modules.prefetch_related('submodules').all():
        dest_mod = ProgramModule.objects.create(
            program=dest, name=mod.name, display_order=mod.display_order, created_by=user,
        )
        module_map[mod.id] = dest_mod
        for sub in mod.submodules.all():
            dest_sub = ProgramSubmodule.objects.create(
                module=dest_mod, name=sub.name, display_order=sub.display_order, created_by=user,
            )
            submodule_map[sub.id] = dest_sub
    for t in source.targets.all():
        reset_sub_items = [
            {k: v for k, v in item.items() if k != 'status'}
            for item in t.sub_items
        ]
        copied = Target.objects.create(
            program=dest,
            name=t.name,
            measurement_type=t.measurement_type,
            sub_items=reset_sub_items,
            sub_item_progression=t.sub_item_progression,
            prompting_template=t.prompting_template,
            sd_text=t.sd_text,
            teaching_instructions=t.teaching_instructions,
            status='waiting',
            display_order=t.display_order,
            is_visible_to_staff=t.is_visible_to_staff,
            module=module_map.get(t.module_id) if t.module_id else None,
            submodule=submodule_map.get(t.submodule_id) if t.submodule_id else None,
            created_by=user,
        )
        if copied.measurement_type in _SUB_ITEM_MEASUREMENT_TYPES:
            _sync_target_sub_items(copied, copied.sub_items, user)
    dest.refresh_from_db()
    return dest


@router.post('/org-programs/{program_id}/assign', response={201: ProgramSchema})
def assign_org_program_to_client(request, program_id: int, data: AssignOrgProgramRequest):
    """Copy a facility-level program template to a specific client."""
    require_permission(request, 'client_programs_create')
    _assert_client_accessible(request, data.client_id)
    try:
        template = _org_qs(request).prefetch_related('targets').get(id=program_id)
    except Program.DoesNotExist:
        raise HttpError(404, 'Program not found')
    dest = _copy_program_to_client(template, data.client_id, request.user)
    return 201, {**_serialize_program(dest, request, include_targets=True)}


@router.post('/programs/{program_id}/copy', response={201: ProgramSchema})
def copy_program_to_client(request, program_id: int, data: AssignOrgProgramRequest):
    """Copy any client program to another client."""
    _require_supervisor(request)
    _assert_client_accessible(request, data.client_id)
    source = _get_program_or_404(request, program_id)
    if source.is_template:
        raise HttpError(404, 'Program not found')
    dest = _copy_program_to_client(source, data.client_id, request.user)
    return 201, {**_serialize_program(dest, request, include_targets=True)}


# ---------------------------------------------------------------------------
# Central library (platform-owned reference programs, importable by any org)
#
# Backed by apps.central_library.CentralProgram/CentralTarget — a plain
# shared-schema model (SHARED_APPS), not a tenant-scoped one. There's exactly
# one catalog, authored by superusers via Django admin; no per-org schema
# switching is needed to read it, unlike apps.programs (still a TENANT_APP).
# ---------------------------------------------------------------------------

def _central_qs():
    return CentralProgram.objects.filter(status=CentralProgram.Status.ACTIVE)


def _central_import_progress(request) -> dict[int, int]:
    """central_program_id -> how many of its targets are already copied into
    the caller's linked org program (0 if there's no linked program at all).
    Used both to show "Already in Library" only once every target has been
    pulled in, and to render a partial-progress fill bar otherwise."""
    rows = (
        Program.objects
        .filter(
            _same_practice_q(request.user, 'created_by__'),
            source_central_program_id__isnull=False,
            is_template=True,
        )
        .annotate(copied_count=models.Count('targets'))
        .values_list('source_central_program_id', 'copied_count')
    )
    return {central_id: count for central_id, count in rows}


def _serialize_central_program(
    program: CentralProgram, request, include_targets: bool = False, import_progress: dict[int, int] = {},
) -> dict:
    targets = []
    if include_targets:
        linked = _find_linked_org_program(program, request.user)
        copied_names = set(linked.targets.values_list('name', flat=True)) if linked else set()
        targets = [
            {
                'id': t['id'], 'name': t['name'], 'status': 'waiting',
                'measurement_type': t['measurement_type'],
                'display_order': t['display_order'], 'is_visible_to_staff': t['is_visible_to_staff'],
                'already_copied': t['name'] in copied_names,
            }
            for t in program.targets.all().values('id', 'name', 'measurement_type', 'display_order', 'is_visible_to_staff')
        ]
    target_count = program.targets.count()
    imported_target_count = min(import_progress.get(program.id, 0), target_count)
    return {
        'id': program.id,
        'is_template': True,
        'name': program.name,
        'category': program.category,
        'status': program.status,
        'phase': program.phase,
        'treatment_area': program.treatment_area,
        'tags': program.tags,
        'objective': program.objective,
        'instructions': program.instructions,
        'prompting_template_id': None,
        'folder_id': program.folder_id,
        'image_url': _optimized_program_image_url(request, program.image),
        'already_imported': target_count > 0 and imported_target_count >= target_count,
        'imported_target_count': imported_target_count,
        'display_order': program.display_order,
        'target_count': target_count,
        'targets': targets,
        'created_at': program.created_at,
        'updated_at': program.updated_at,
    }


@router.get('/central-programs', response=list[OrgProgramSchema])
def list_central_programs(
    request, category: str | None = None,
    folder_id: int | None = None, unfiled: bool = False,
):
    require_permission(request, 'central_library_view')
    qs = _central_qs()
    if category:
        qs = qs.filter(category=category)
    if folder_id is not None:
        qs = qs.filter(folder_id=folder_id)
    elif unfiled:
        qs = qs.filter(folder_id__isnull=True)
    import_progress = _central_import_progress(request)
    return [_serialize_central_program(p, request, import_progress=import_progress) for p in qs.prefetch_related('targets')]


# Registered before /central-programs/{program_id} below — same routing
# gotcha as apps.programs.api's org-program folders: Django's resolver
# matches in registration order, and an untyped path param would otherwise
# swallow the literal 'folders' segment and 405 instead of reaching these.
@router.get('/central-programs/folders', response=list[CentralProgramFolderSchema])
def list_central_program_folders(request):
    require_permission(request, 'central_library_view')
    return [
        {
            'id': f.id,
            'name': f.name,
            'display_order': f.display_order,
            'program_count': f.programs.filter(status=CentralProgram.Status.ACTIVE).count(),
        }
        for f in CentralProgramFolder.objects.all()
    ]


@router.post('/central-programs/folders/{folder_id}/import', response={201: ImportCentralFolderResult})
def import_central_folder(request, folder_id: int):
    """Import every not-yet-imported program in a Central Library folder into
    the caller's org library in one shot, filing them all into a same-named
    org ProgramFolder (created if it doesn't already exist)."""
    require_permission(request, 'central_library_import')
    try:
        central_folder = CentralProgramFolder.objects.get(id=folder_id)
    except CentralProgramFolder.DoesNotExist:
        raise HttpError(404, 'Folder not found')

    org_folder, _created = ProgramFolder.objects.get_or_create(
        name=central_folder.name,
        defaults={'created_by': request.user},
    )
    import_progress = _central_import_progress(request)
    imported_count = 0
    skipped_count = 0
    for central_program in central_folder.programs.filter(status=CentralProgram.Status.ACTIVE):
        target_count = central_program.targets.count()
        copied_count = import_progress.get(central_program.id, 0)
        if target_count > 0 and copied_count >= target_count:
            skipped_count += 1
            continue
        # Also covers a program only partially copied so far (e.g. via a
        # prior single-target copy) — _clone_central_program tops it up
        # with whatever targets it's still missing rather than duplicating it.
        dest = _clone_central_program(central_program.id, request.user)
        if dest.folder_id is None:  # don't move a program the caller already filed elsewhere
            dest.folder = org_folder
            dest.save(update_fields=['folder'])
        imported_count += 1

    return 201, {
        'folder_id': org_folder.id,
        'folder_name': org_folder.name,
        'imported_count': imported_count,
        'skipped_count': skipped_count,
    }


@router.get('/central-programs/{program_id}', response=OrgProgramSchema)
def get_central_program(request, program_id: int):
    require_permission(request, 'central_library_view')
    try:
        program = _central_qs().prefetch_related('targets').get(id=program_id)
    except CentralProgram.DoesNotExist:
        raise HttpError(404, 'Program not found')
    return _serialize_central_program(program, request, include_targets=True, import_progress=_central_import_progress(request))


def _find_linked_org_program(source: CentralProgram, user) -> Program | None:
    """The caller's own program already linked to this Central Library
    program, if any — whether that link was made by a prior whole-program
    import or by a prior single-target copy. Both funnel through this so a
    central program never ends up duplicated across multiple org programs."""
    return (
        Program.objects
        .filter(_same_practice_q(user, 'created_by__'), source_central_program_id=source.id, is_template=True)
        .order_by('id')
        .first()
    )


def _create_linked_org_program(source: CentralProgram, user) -> Program:
    dest = Program.objects.create(
        is_template=True,
        external_client_id=None,
        status=Program.Status.ACTIVE,
        name=source.name,
        category=source.category,
        phase=source.phase,
        treatment_area=source.treatment_area,
        tags=source.tags,
        objective=source.objective,
        instructions=source.instructions,
        source_central_program_id=source.id,
        display_order=source.display_order,
        created_by=user,
    )
    _copy_image(source.image, dest)
    return dest


def _append_central_targets(dest: Program, targets, user) -> None:
    """Copies each given CentralTarget into dest as a new Target, appended
    after whatever targets dest already has."""
    start_order = dest.targets.count()
    for i, t in enumerate(targets):
        prompting_template = None
        if t.prompting_levels:
            prompting_template = PromptingTemplate.objects.create(name=f'{t.name} Prompting', levels=t.prompting_levels)
        copied = Target.objects.create(
            program=dest,
            name=t.name,
            measurement_type=t.measurement_type,
            sub_items=t.sub_items,
            sd_text=t.sd_text,
            teaching_instructions=t.teaching_instructions,
            display_order=start_order + i,
            is_visible_to_staff=t.is_visible_to_staff,
            prompting_template=prompting_template,
            created_by=user,
        )
        if copied.measurement_type in _SUB_ITEM_MEASUREMENT_TYPES:
            _sync_target_sub_items(copied, copied.sub_items, user)


def _clone_central_program(program_id: int, user) -> Program:
    """Deep-copies a Central Library program (+ targets) into the calling
    user's own organization. A target's optional `prompting_levels` becomes
    a new org-owned PromptingTemplate — PromptingTemplate is tenant-scoped,
    so cloning (not referencing) is the only option, and it also means each
    org gets its own editable copy rather than a shared read-only one.

    If this central program is already linked to one of the caller's own
    programs — e.g. because a target was copied individually before the
    whole program was — this tops that program up with whatever targets it
    is still missing, rather than creating a second, duplicate program.
    """
    try:
        source = _central_qs().prefetch_related('targets').get(id=program_id)
    except CentralProgram.DoesNotExist:
        raise HttpError(404, 'Program not found')

    dest = _find_linked_org_program(source, user)
    if dest is not None:
        existing_names = set(dest.targets.values_list('name', flat=True))
        missing = [t for t in source.targets.all() if t.name not in existing_names]
        if not missing:
            raise HttpError(409, f'"{source.name}" is already in your Program Library.')
        _append_central_targets(dest, missing, user)
        dest.refresh_from_db()
        return dest

    dest = _create_linked_org_program(source, user)
    # Central programs have no modules/submodules, so no mapping needed here.
    _append_central_targets(dest, source.targets.all(), user)
    dest.refresh_from_db()
    return dest


@router.post('/central-programs/{program_id}/import', response={201: OrgProgramSchema})
def import_central_program(request, program_id: int):
    """Import a Central Library program into the caller's own org library."""
    require_permission(request, 'central_library_import')
    dest = _clone_central_program(program_id, request.user)
    return 201, _serialize_org_program(dest, request, include_targets=True)


def _clone_central_target_as_program(program_id: int, target_id: int, user) -> Program:
    """Copy a single target out of a Central Library program into the
    caller's Library. If this central program is already linked to one of
    the caller's own programs — a prior whole-program import, or a prior
    single-target copy — the target is pulled into that same program
    instead of spinning up a duplicate. Only when no linked program exists
    yet is a new one created, mirroring the source program's metadata
    (name, category, image, etc.) so it starts out as a coherent program
    rather than an orphaned single target."""
    try:
        source = _central_qs().get(id=program_id)
    except CentralProgram.DoesNotExist:
        raise HttpError(404, 'Program not found')
    try:
        t = source.targets.get(id=target_id)
    except ObjectDoesNotExist:
        raise HttpError(404, 'Target not found')

    dest = _find_linked_org_program(source, user)
    if dest is not None:
        if dest.targets.filter(name=t.name).exists():
            return dest  # already pulled in — nothing new to do
        _append_central_targets(dest, [t], user)
        dest.refresh_from_db()
        return dest

    dest = _create_linked_org_program(source, user)
    _append_central_targets(dest, [t], user)
    dest.refresh_from_db()
    return dest


@router.post('/central-programs/{program_id}/targets/{target_id}/import', response={201: OrgProgramSchema})
def import_central_target(request, program_id: int, target_id: int):
    """Copy a single target out of a Central Library program — lands as its
    own new program in the caller's Library (see import_central_program to
    copy the whole program with all its targets instead)."""
    require_permission(request, 'central_library_import')
    dest = _clone_central_target_as_program(program_id, target_id, request.user)
    return 201, _serialize_org_program(dest, request, include_targets=True)


# ---------------------------------------------------------------------------
# Program Modules & Submodules
# ---------------------------------------------------------------------------

def _serialize_module(module: ProgramModule) -> dict:
    submodule_target_counts = {
        row['submodule_id']: row['count']
        for row in module.targets.filter(submodule_id__isnull=False)
        .values('submodule_id')
        .annotate(count=models.Count('id'))
    }
    return {
        'id': module.id,
        'program_id': module.program_id,
        'name': module.name,
        'display_order': module.display_order,
        'target_count': module.targets.count(),
        'submodules': [
            {
                'id': s.id,
                'module_id': s.module_id,
                'name': s.name,
                'display_order': s.display_order,
                'target_count': submodule_target_counts.get(s.id, 0),
                'created_at': s.created_at,
                'updated_at': s.updated_at,
            }
            for s in module.submodules.all()
        ],
        'created_at': module.created_at,
        'updated_at': module.updated_at,
    }


@router.get('/programs/{program_id}/modules', response=list[ProgramModuleSchema])
def list_modules(request, program_id: int):
    _get_program_or_404(request, program_id)
    return [_serialize_module(m) for m in ProgramModule.objects.filter(program_id=program_id).prefetch_related('submodules')]


@router.post('/programs/{program_id}/modules', response={201: ProgramModuleSchema})
def create_module(request, program_id: int, data: ProgramModuleRequest):
    _require_supervisor(request)
    program = _get_program_or_404(request, program_id)
    module = ProgramModule.objects.create(
        program=program,
        name=data.name,
        display_order=data.display_order,
        created_by=request.user,
    )
    return 201, _serialize_module(module)


@router.patch('/programs/{program_id}/modules/{module_id}', response=ProgramModuleSchema)
def update_module(request, program_id: int, module_id: int, data: ProgramModuleRequest):
    _require_supervisor(request)
    _get_program_or_404(request, program_id)
    try:
        module = ProgramModule.objects.prefetch_related('submodules').get(id=module_id, program_id=program_id)
    except ProgramModule.DoesNotExist:
        raise HttpError(404, 'Module not found')
    for k, v in data.dict().items():
        setattr(module, k, v)
    module.save()
    return _serialize_module(module)


@router.delete('/programs/{program_id}/modules/{module_id}', response={204: None})
def delete_module(request, program_id: int, module_id: int):
    _require_supervisor(request)
    _get_program_or_404(request, program_id)
    try:
        ProgramModule.objects.get(id=module_id, program_id=program_id).delete()
    except ProgramModule.DoesNotExist:
        raise HttpError(404, 'Module not found')
    return 204, None


@router.post('/programs/{program_id}/modules/reorder', response={200: None})
def reorder_modules(request, program_id: int, data: ReorderModulesRequest):
    _require_supervisor(request)
    _get_program_or_404(request, program_id)
    for order, module_id in enumerate(data.ordered_ids):
        ProgramModule.objects.filter(id=module_id, program_id=program_id).update(display_order=order)
    return 200, None


@router.post('/programs/{program_id}/modules/{module_id}/submodules', response={201: ProgramSubmoduleSchema})
def create_submodule(request, program_id: int, module_id: int, data: ProgramSubmoduleRequest):
    _require_supervisor(request)
    _get_program_or_404(request, program_id)
    try:
        module = ProgramModule.objects.get(id=module_id, program_id=program_id)
    except ProgramModule.DoesNotExist:
        raise HttpError(404, 'Module not found')
    submodule = ProgramSubmodule.objects.create(
        module=module,
        name=data.name,
        display_order=data.display_order,
        created_by=request.user,
    )
    return 201, {
        'id': submodule.id,
        'module_id': submodule.module_id,
        'name': submodule.name,
        'display_order': submodule.display_order,
        'created_at': submodule.created_at,
        'updated_at': submodule.updated_at,
    }


@router.patch('/programs/{program_id}/modules/{module_id}/submodules/{submodule_id}', response=ProgramSubmoduleSchema)
def update_submodule(request, program_id: int, module_id: int, submodule_id: int, data: ProgramSubmoduleRequest):
    _require_supervisor(request)
    _get_program_or_404(request, program_id)
    try:
        submodule = ProgramSubmodule.objects.get(id=submodule_id, module_id=module_id)
    except ProgramSubmodule.DoesNotExist:
        raise HttpError(404, 'Submodule not found')
    for k, v in data.dict().items():
        setattr(submodule, k, v)
    submodule.save()
    return {
        'id': submodule.id,
        'module_id': submodule.module_id,
        'name': submodule.name,
        'display_order': submodule.display_order,
        'created_at': submodule.created_at,
        'updated_at': submodule.updated_at,
    }


@router.delete('/programs/{program_id}/modules/{module_id}/submodules/{submodule_id}', response={204: None})
def delete_submodule(request, program_id: int, module_id: int, submodule_id: int):
    _require_supervisor(request)
    _get_program_or_404(request, program_id)
    try:
        ProgramSubmodule.objects.get(id=submodule_id, module_id=module_id).delete()
    except ProgramSubmodule.DoesNotExist:
        raise HttpError(404, 'Submodule not found')
    return 204, None


@router.post('/programs/{program_id}/modules/{module_id}/submodules/reorder', response={200: None})
def reorder_submodules(request, program_id: int, module_id: int, data: ReorderSubmodulesRequest):
    _require_supervisor(request)
    _get_program_or_404(request, program_id)
    for order, submodule_id in enumerate(data.ordered_ids):
        ProgramSubmodule.objects.filter(id=submodule_id, module_id=module_id).update(display_order=order)
    return 200, None


# ---------------------------------------------------------------------------
# Treatment Areas
# ---------------------------------------------------------------------------

@router.get('/programs/settings/treatment-areas', response=list[TreatmentAreaSchema])
def list_treatment_areas(request, include_inactive: bool = False):
    qs = _settings_qs(TreatmentArea, request)
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs)


@router.post('/programs/settings/treatment-areas', response={201: TreatmentAreaSchema})
def create_treatment_area(request, data: TreatmentAreaRequest):
    _require_settings_permission(request, 'settings_treatment_areas_create')
    _check_unique_name(TreatmentArea, request, data.name)
    return 201, TreatmentArea.objects.create(created_by=request.user, **data.dict())


@router.patch('/programs/settings/treatment-areas/{pk}', response=TreatmentAreaSchema)
def update_treatment_area(request, pk: int, data: TreatmentAreaRequest):
    _require_settings_permission(request, 'settings_treatment_areas_edit')
    try:
        obj = _settings_qs(TreatmentArea, request).get(id=pk)
    except TreatmentArea.DoesNotExist:
        raise HttpError(404, 'Not found')
    _check_unique_name(TreatmentArea, request, data.name, exclude_id=pk)
    for k, v in data.dict(exclude_none=True).items():
        setattr(obj, k, v)
    obj.save()
    return obj


@router.delete('/programs/settings/treatment-areas/{pk}', response={204: None})
def delete_treatment_area(request, pk: int):
    _require_settings_permission(request, 'settings_treatment_areas_delete')
    try:
        _settings_qs(TreatmentArea, request).get(id=pk).delete()
    except TreatmentArea.DoesNotExist:
        raise HttpError(404, 'Not found')
    return 204, None


# ---------------------------------------------------------------------------
# Program Tags
# ---------------------------------------------------------------------------

@router.get('/programs/settings/tags', response=list[ProgramTagSchema])
def list_program_tags(request, include_inactive: bool = False):
    qs = _settings_qs(ProgramTag, request)
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs)


@router.post('/programs/settings/tags', response={201: ProgramTagSchema})
def create_program_tag(request, data: ProgramTagRequest):
    _require_settings_permission(request, 'settings_tags_create')
    _check_unique_name(ProgramTag, request, data.name)
    return 201, ProgramTag.objects.create(created_by=request.user, **data.dict())


@router.patch('/programs/settings/tags/{pk}', response=ProgramTagSchema)
def update_program_tag(request, pk: int, data: ProgramTagRequest):
    _require_settings_permission(request, 'settings_tags_edit')
    try:
        obj = _settings_qs(ProgramTag, request).get(id=pk)
    except ProgramTag.DoesNotExist:
        raise HttpError(404, 'Not found')
    _check_unique_name(ProgramTag, request, data.name, exclude_id=pk)
    for k, v in data.dict(exclude_none=True).items():
        setattr(obj, k, v)
    obj.save()
    return obj


@router.delete('/programs/settings/tags/{pk}', response={204: None})
def delete_program_tag(request, pk: int):
    _require_settings_permission(request, 'settings_tags_delete')
    try:
        _settings_qs(ProgramTag, request).get(id=pk).delete()
    except ProgramTag.DoesNotExist:
        raise HttpError(404, 'Not found')
    return 204, None


# ---------------------------------------------------------------------------
# Target Statuses
# ---------------------------------------------------------------------------

@router.get('/programs/settings/statuses', response=list[TargetStatusSchema])
def list_target_statuses(request, include_inactive: bool = False):
    qs = _settings_qs(TargetStatus, request, include_org_defaults=True)
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs)

@router.post('/programs/settings/statuses', response={201: TargetStatusSchema})
def create_target_status(request, data: TargetStatusRequest):
    if not request.user.is_superuser:
        raise HttpError(403, 'Only a superuser can add a status')
    if _settings_qs(TargetStatus, request, include_org_defaults=True).filter(key=data.key).exists():
        raise HttpError(409, f'A status with key "{data.key}" already exists')
    payload = data.dict()
    if payload['is_default'] and not payload['is_active']:
        raise HttpError(400, 'An inactive status cannot be the default')
    if payload['is_default']:
        _settings_qs(TargetStatus, request, include_org_defaults=True).filter(is_default=True).update(is_default=False)
    return 201, TargetStatus.objects.create(created_by=request.user, **payload)


@router.patch('/programs/settings/statuses/{pk}', response=TargetStatusSchema)
def update_target_status(request, pk: int, data: TargetStatusUpdateRequest):
    _require_settings_permission(request, 'settings_statuses_edit')
    try:
        obj = _settings_qs(TargetStatus, request, include_org_defaults=True).get(id=pk)
    except TargetStatus.DoesNotExist:
        raise HttpError(404, 'Not found')
    update = data.dict(exclude_none=True)
    is_default = update.get('is_default', obj.is_default)
    is_active = update.get('is_active', obj.is_active)
    if is_default and not is_active:
        raise HttpError(400, 'An inactive status cannot be the default')
    if update.get('is_default'):
        _settings_qs(TargetStatus, request, include_org_defaults=True).filter(is_default=True).exclude(id=pk).update(is_default=False)
    for k, v in update.items():
        setattr(obj, k, v)
    obj.save()
    return obj


@router.delete('/programs/settings/statuses/{pk}', response={204: None})
def delete_target_status(request, pk: int):
    _require_settings_permission(request, 'settings_statuses_delete')
    try:
        obj = _settings_qs(TargetStatus, request, include_org_defaults=True).get(id=pk)
    except TargetStatus.DoesNotExist:
        raise HttpError(404, 'Not found')
    if obj.created_by_id is None and not request.user.is_superuser:
        raise HttpError(403, 'Only a superuser can delete a built-in default status')
    obj.delete()
    return 204, None


# ---------------------------------------------------------------------------
# Program Data Fields
# ---------------------------------------------------------------------------

@router.get('/programs/settings/data-fields', response=list[ProgramDataFieldSchema])
def list_data_fields(request, include_inactive: bool = False):
    qs = _settings_qs(ProgramDataField, request)
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return list(qs)


@router.post('/programs/settings/data-fields', response={201: ProgramDataFieldSchema})
def create_data_field(request, data: ProgramDataFieldRequest):
    _require_settings_permission(request, 'settings_data_fields_create')
    _check_unique_name(ProgramDataField, request, data.name)
    return 201, ProgramDataField.objects.create(created_by=request.user, **data.dict())


@router.patch('/programs/settings/data-fields/{pk}', response=ProgramDataFieldSchema)
def update_data_field(request, pk: int, data: ProgramDataFieldRequest):
    _require_settings_permission(request, 'settings_data_fields_edit')
    try:
        obj = _settings_qs(ProgramDataField, request).get(id=pk)
    except ProgramDataField.DoesNotExist:
        raise HttpError(404, 'Not found')
    _check_unique_name(ProgramDataField, request, data.name, exclude_id=pk)
    for k, v in data.dict(exclude_none=True).items():
        setattr(obj, k, v)
    obj.save()
    return obj


@router.delete('/programs/settings/data-fields/{pk}', response={204: None})
def delete_data_field(request, pk: int):
    _require_settings_permission(request, 'settings_data_fields_delete')
    try:
        _settings_qs(ProgramDataField, request).get(id=pk).delete()
    except ProgramDataField.DoesNotExist:
        raise HttpError(404, 'Not found')
    return 204, None
