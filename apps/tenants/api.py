import re

from django.conf import settings
from django.db import transaction
from django_tenants.utils import schema_context
from ninja import Router
from ninja.errors import HttpError

from .models import Domain, Organization, OrganizationTpmsAdminId
from .schemas import (
    OrganizationAuthenticationSettingsSchema,
    OrganizationAuthenticationSettingsUpdate,
    OrganizationPracticeEmailSettingsSchema,
    OrganizationSuperadminCreate,
    OrganizationSuperadminUpdate,
    TpmsAdminIdCreate,
    TpmsAdminEmailSettingSchema,
    TpmsAdminEmailSettingUpdate,
)

router = Router()


def _require_manager(request) -> None:
    if not request.user.has_role('admin', 'supervisor'):
        raise HttpError(403, 'Manager access required')


def _require_superadmin(request) -> None:
    if not getattr(request.user, 'is_superuser', False):
        raise HttpError(403, 'Superadmin access required')


def _organization(request) -> Organization:
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise HttpError(400, 'No organization context')
    return tenant


def _normalize_slug(value: str) -> str:
    slug = re.sub(r'[^a-z0-9-]+', '-', value.lower().strip()).strip('-')
    if not slug:
        raise HttpError(400, 'Organization slug is required')
    return slug


def _schema_from_slug(slug: str) -> str:
    return slug.replace('-', '_')


def _validate_schema_name(schema_name: str) -> None:
    if not re.match(r'^[a-z][a-z0-9_]{1,61}$', schema_name):
        raise HttpError(
            400,
            'Schema name must start with a letter and contain only lowercase letters, numbers, and underscores.',
        )


def _validate_plan(plan: str) -> None:
    if plan not in Organization.Plan.values:
        raise HttpError(400, 'Invalid organization plan')


def _domain_for_slug(slug: str) -> str:
    base_domain = getattr(settings, 'TENANT_BASE_DOMAIN', '') or ''
    if base_domain:
        return f'{slug}.{base_domain}'.lower()
    return f'{slug}.localhost'


def _serialize_superadmin_org(org: Organization) -> dict:
    domains = list(org.domains.all())
    primary_domain = next((domain.domain for domain in domains if domain.is_primary), None)
    fallback_domain = domains[0].domain if domains else None
    return {
        'id': org.id,
        'name': org.name,
        'slug': org.slug,
        'schema_name': org.schema_name,
        'plan': org.plan,
        'is_active': org.is_active,
        'domain': primary_domain or fallback_domain,
        'tpms_admin_ids': [
                {
                    'id': row.id,
                    'admin_id': row.admin_id,
                    'facility_name': row.facility_name,
                    'email_notifications_enabled': row.email_notifications_enabled,
                }
            for row in org.tpms_admin_ids.all()
        ],
    }


@router.get('/settings/authentication', response=OrganizationAuthenticationSettingsSchema)
def get_authentication_settings(request):
    _require_manager(request)
    org = _organization(request)
    return {
        'automatic_logout_enabled': org.automatic_logout_enabled,
        'automatic_logout_minutes': org.automatic_logout_minutes,
    }


def _serialize_practice_email_settings() -> list[dict]:
    organizations = Organization.objects.prefetch_related('tpms_admin_ids', 'domains').order_by('name', 'id')
    return [_serialize_superadmin_org(org) for org in organizations]


@router.get('/superadmin/practice-email-settings', response=list[OrganizationPracticeEmailSettingsSchema])
def list_superadmin_practice_email_settings(request):
    _require_superadmin(request)
    return _serialize_practice_email_settings()


@router.post('/superadmin/organizations', response={201: OrganizationPracticeEmailSettingsSchema})
def create_superadmin_organization(request, data: OrganizationSuperadminCreate):
    _require_superadmin(request)
    slug = _normalize_slug(data.slug or data.name)
    schema_name = (data.schema_name or _schema_from_slug(slug)).lower()
    domain = (data.domain or _domain_for_slug(slug)).lower()
    _validate_schema_name(schema_name)
    _validate_plan(data.plan)
    if Organization.objects.filter(slug=slug).exists():
        raise HttpError(409, 'Organization slug already exists')
    if Organization.objects.filter(schema_name=schema_name).exists():
        raise HttpError(409, 'Organization schema already exists')
    if Domain.objects.filter(domain=domain).exists():
        raise HttpError(409, 'Organization domain already exists')
    seed_admin_ids = [item.admin_id for item in data.tpms_admin_ids]
    duplicate_admin_ids = OrganizationTpmsAdminId.objects.filter(admin_id__in=seed_admin_ids)
    if duplicate_admin_ids.exists():
        taken = ', '.join(str(admin_id) for admin_id in duplicate_admin_ids.values_list('admin_id', flat=True))
        raise HttpError(409, f'TPMS admin ID(s) already mapped: {taken}')

    with transaction.atomic():
        org = Organization.objects.create(
            name=data.name.strip(),
            slug=slug,
            schema_name=schema_name,
            plan=data.plan,
            is_active=data.is_active,
        )
        Domain.objects.create(tenant=org, domain=domain, is_primary=True)
        from apps.tenants.services import copy_default_target_statuses_to_org
        with schema_context(schema_name):
            copy_default_target_statuses_to_org(org.id)
        for item in data.tpms_admin_ids:
            OrganizationTpmsAdminId.objects.create(
                organization=org,
                admin_id=item.admin_id,
                facility_name=item.facility_name.strip(),
            )

    return 201, _serialize_superadmin_org(
        Organization.objects.prefetch_related('tpms_admin_ids', 'domains').get(id=org.id)
    )


@router.patch('/superadmin/organizations/{organization_id}', response=OrganizationPracticeEmailSettingsSchema)
def update_superadmin_organization(request, organization_id: int, data: OrganizationSuperadminUpdate):
    _require_superadmin(request)
    try:
        org = Organization.objects.prefetch_related('tpms_admin_ids', 'domains').get(id=organization_id)
    except Organization.DoesNotExist:
        raise HttpError(404, 'Organization not found')

    update_fields = []
    if data.name is not None:
        org.name = data.name.strip()
        update_fields.append('name')
    if data.slug is not None:
        slug = _normalize_slug(data.slug)
        if Organization.objects.exclude(id=org.id).filter(slug=slug).exists():
            raise HttpError(409, 'Organization slug already exists')
        org.slug = slug
        update_fields.append('slug')
    if data.plan is not None:
        _validate_plan(data.plan)
        org.plan = data.plan
        update_fields.append('plan')
    if data.is_active is not None:
        org.is_active = data.is_active
        update_fields.append('is_active')
    if update_fields:
        update_fields.append('updated_at')
        org.save(update_fields=update_fields)
    if data.domain is not None:
        domain = data.domain.lower().strip()
        if not domain:
            raise HttpError(400, 'Domain is required')
        if Domain.objects.exclude(tenant=org).filter(domain=domain).exists():
            raise HttpError(409, 'Organization domain already exists')
        primary = org.domains.filter(is_primary=True).first()
        if primary:
            primary.domain = domain
            primary.save(update_fields=['domain'])
        else:
            Domain.objects.create(tenant=org, domain=domain, is_primary=True)

    org = Organization.objects.prefetch_related('tpms_admin_ids', 'domains').get(id=org.id)
    return _serialize_superadmin_org(org)


@router.post('/superadmin/tpms-admin-ids', response={201: TpmsAdminEmailSettingSchema})
def create_superadmin_tpms_admin_id(request, data: TpmsAdminIdCreate):
    _require_superadmin(request)
    try:
        org = Organization.objects.get(id=data.organization_id)
    except Organization.DoesNotExist:
        raise HttpError(404, 'Organization not found')
    if OrganizationTpmsAdminId.objects.filter(admin_id=data.admin_id).exists():
        raise HttpError(409, 'TPMS admin ID already mapped')
    row = OrganizationTpmsAdminId.objects.create(
        organization=org,
        admin_id=data.admin_id,
        facility_name=data.facility_name.strip(),
        email_notifications_enabled=data.email_notifications_enabled,
    )
    return 201, {
        'id': row.id,
        'admin_id': row.admin_id,
        'facility_name': row.facility_name,
        'email_notifications_enabled': row.email_notifications_enabled,
    }


@router.patch('/superadmin/practice-email-settings/{mapping_id}', response=TpmsAdminEmailSettingSchema)
def update_superadmin_practice_email_setting(
    request,
    mapping_id: int,
    data: TpmsAdminEmailSettingUpdate,
):
    _require_superadmin(request)
    try:
        row = OrganizationTpmsAdminId.objects.get(id=mapping_id)
    except OrganizationTpmsAdminId.DoesNotExist:
        raise HttpError(404, 'TPMS admin ID not found')

    update_fields = []
    if data.admin_id is not None:
        if OrganizationTpmsAdminId.objects.exclude(id=row.id).filter(admin_id=data.admin_id).exists():
            raise HttpError(409, 'TPMS admin ID already mapped')
        row.admin_id = data.admin_id
        update_fields.append('admin_id')
    if data.facility_name is not None:
        row.facility_name = data.facility_name.strip()
        update_fields.append('facility_name')
    if data.email_notifications_enabled is not None:
        row.email_notifications_enabled = data.email_notifications_enabled
        update_fields.append('email_notifications_enabled')
    if update_fields:
        row.save(update_fields=update_fields)
    return {
        'id': row.id,
        'admin_id': row.admin_id,
        'facility_name': row.facility_name,
        'email_notifications_enabled': row.email_notifications_enabled,
    }


@router.delete('/superadmin/practice-email-settings/{mapping_id}', response={204: None})
def delete_superadmin_tpms_admin_id(request, mapping_id: int):
    _require_superadmin(request)
    try:
        row = OrganizationTpmsAdminId.objects.get(id=mapping_id)
    except OrganizationTpmsAdminId.DoesNotExist:
        raise HttpError(404, 'TPMS admin ID not found')
    row.delete()
    return 204, None


@router.patch('/settings/authentication', response=OrganizationAuthenticationSettingsSchema)
def update_authentication_settings(request, data: OrganizationAuthenticationSettingsUpdate):
    _require_manager(request)
    org = _organization(request)

    update_fields = []
    if data.automatic_logout_enabled is not None:
        org.automatic_logout_enabled = data.automatic_logout_enabled
        update_fields.append('automatic_logout_enabled')
    if data.automatic_logout_minutes is not None:
        valid_minutes = {choice.value for choice in Organization.AutomaticLogoutMinutes}
        if data.automatic_logout_minutes not in valid_minutes:
            raise HttpError(400, 'Automatic logout timer must be 2 minutes, 9 hours, 24 hours, or 1 week')
        org.automatic_logout_minutes = data.automatic_logout_minutes
        update_fields.append('automatic_logout_minutes')

    if update_fields:
        update_fields.append('updated_at')
        org.save(update_fields=update_fields)

    return {
        'automatic_logout_enabled': org.automatic_logout_enabled,
        'automatic_logout_minutes': org.automatic_logout_minutes,
    }
