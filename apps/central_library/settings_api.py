from ninja import Router, Schema
from ninja.errors import HttpError
from django.db import transaction, IntegrityError
from pydantic import Field

from apps.accounts.auth import partner_auth
from shared.schema_types import NameStr200
from .models import CentralProgramSetting
from .settings_service import normalize_configuration, validate_dependencies

router = Router(auth=partner_auth)


class SettingRequest(Schema):
    kind: CentralProgramSetting.Kind
    name: NameStr200
    configuration: dict = {}
    dependency_ids: list[int] = Field(default=[], max_length=100)
    is_active: bool = True


class SettingSchema(SettingRequest):
    id: int


def serialize_setting(setting):
    return dict(id=setting.pk, kind=setting.kind, name=setting.name, configuration=setting.configuration,
                dependency_ids=list(setting.dependencies.values_list('id', flat=True)), is_active=setting.is_active)


def require_admin(request):
    from apps.programs.api import _require_superadmin
    _require_superadmin(request)


@router.get('/superadmin/central-settings', response=list[SettingSchema])
def list_settings(request):
    require_admin(request)
    return [serialize_setting(s) for s in CentralProgramSetting.objects.prefetch_related('dependencies')]


def save_setting(request, data, pk=None):
    require_admin(request)
    configuration = normalize_configuration(data.kind, data.name, data.configuration)
    dependencies = validate_dependencies(data.kind, configuration, data.dependency_ids)
    setting = None
    if pk is not None:
        setting = CentralProgramSetting.objects.filter(pk=pk).first()
        if setting is None:
            raise HttpError(404, 'Central setting not found')
        if setting.kind != data.kind:
            raise HttpError(400, 'A linked setting cannot change its kind')
        if setting.kind == 'target_status' and setting.configuration.get('key') != configuration.get('key'):
            raise HttpError(400, 'Status keys cannot change after creation; create a new status instead')
    if data.kind == 'target_status':
        duplicates = CentralProgramSetting.objects.filter(kind='target_status', configuration__key=configuration['key'])
        if pk is not None:
            duplicates = duplicates.exclude(pk=pk)
        if duplicates.exists():
            raise HttpError(409, 'A central target status with this key already exists')
    try:
        with transaction.atomic():
            if setting is None:
                setting = CentralProgramSetting.objects.create(kind=data.kind, name=data.name,
                    configuration=configuration, is_active=data.is_active)
            else:
                setting.name, setting.configuration, setting.is_active = data.name, configuration, data.is_active
                setting.save()
            setting.dependencies.set(dependencies)
            if setting.kind in {'treatment_area', 'tag'}:
                # Program stores display names for compatibility with facility
                # programs; keep those mirrors current when central names change.
                for program in setting.programs.all():
                    area = program.settings.filter(kind='treatment_area').first()
                    if area:
                        program.treatment_area = area.name
                    program.tags = list(program.settings.filter(kind='tag').values_list('name', flat=True))
                    program.save(update_fields=['treatment_area', 'tags', 'updated_at'])
    except IntegrityError as exc:
        raise HttpError(409, 'A central setting with this kind and name already exists') from exc
    return serialize_setting(setting)


@router.post('/superadmin/central-settings', response={201: SettingSchema})
def create_setting(request, data: SettingRequest):
    return 201, save_setting(request, data)


@router.put('/superadmin/central-settings/{pk}', response=SettingSchema)
def update_setting(request, pk: int, data: SettingRequest):
    return save_setting(request, data, pk)


@router.get('/central-settings', response=list[SettingSchema])
def view_settings(request):
    from apps.accounts.permissions import require_permission
    require_permission(request, 'central_library_view')
    return [serialize_setting(s) for s in CentralProgramSetting.objects.filter(is_active=True).prefetch_related('dependencies')]
