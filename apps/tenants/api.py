import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import schema_context
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.models import APIKey, User
from apps.integrations.models import PullJob
from .models import Domain, Organization, OrganizationTpmsAdminId
from .schemas import (
    OrganizationAuthenticationSettingsSchema,
    OrganizationAuthenticationSettingsUpdate,
    OrganizationIntegrationSettingsSchema,
    OrganizationIntegrationSettingsUpdate,
    OrganizationPracticeEmailSettingsSchema,
    PullJobSchema,
    TherapyPmsConnectRequest,
    TherapyPmsPullAppointmentsRequest,
    OrganizationSuperadminCreate,
    OrganizationSuperadminUpdate,
    SuperadminAPIKeyCreate,
    SuperadminAPIKeyCreatedSchema,
    SuperadminAPIKeySchema,
    SuperadminUserCreate,
    SuperadminUserSchema,
    SuperadminUserUpdate,
    TpmsAdminIdCreate,
    TpmsAdminEmailSettingSchema,
    TpmsAdminEmailSettingUpdate,
    ImpersonateRequest,
    ImpersonationLogSchema,
)

router = Router()
logger = logging.getLogger(__name__)


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


def _default_integration_facility(request):
    org = _organization(request)
    facilities = org.tpms_admin_ids.all()
    login_admin_id = getattr(request.user, 'external_admin_id', None)
    if isinstance(login_admin_id, int):
        return facilities.filter(admin_id=login_admin_id).first()
    email = getattr(request.user, 'email', None)
    if isinstance(email, str) and email:
        matches = list(facilities.filter(integration_email__iexact=email)[:2])
        if len(matches) == 1:
            return matches[0]
    candidates = list(facilities[:2])
    if len(candidates) == 1:
        return candidates[0]
    return None


def _integration_connection(request, facility_admin_id: int | None = None):
    from apps.integrations.tpms_pull import FacilityConnection
    org = _organization(request)
    facilities = org.tpms_admin_ids.all()
    if facility_admin_id is not None:
        facility = facilities.filter(admin_id=facility_admin_id).first()
        if facility is None:
            raise HttpError(404, 'Facility not found in this organization')
    else:
        facility = _default_integration_facility(request)
        if facility is None:
            raise HttpError(400, 'Connect with your TherapyPMS admin credentials to identify the facility.')
    return FacilityConnection(org, facility)


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
        'pin_lock_allowed': getattr(org, 'pin_lock_allowed', False),
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
            raise HttpError(400, f'Automatic logout timer must be one of: {sorted(valid_minutes)} minutes')
        org.automatic_logout_minutes = data.automatic_logout_minutes
        update_fields.append('automatic_logout_minutes')
    if data.pin_lock_allowed is not None:
        org.pin_lock_allowed = data.pin_lock_allowed
        update_fields.append('pin_lock_allowed')

    if update_fields:
        update_fields.append('updated_at')
        org.save(update_fields=update_fields)

    return {
        'automatic_logout_enabled': org.automatic_logout_enabled,
        'automatic_logout_minutes': org.automatic_logout_minutes,
        'pin_lock_allowed': org.pin_lock_allowed,
    }


@router.get('/settings/integrations', response=OrganizationIntegrationSettingsSchema)
def get_integration_settings(request, facility_admin_id: int | None = None):
    _require_manager(request)
    org = _organization(request)
    facilities = list(org.tpms_admin_ids.all())
    connection = None
    if facility_admin_id is not None or _default_integration_facility(request) is not None:
        connection = _integration_connection(request, facility_admin_id)
    return {
        'integration_platform': connection.integration_platform if connection else '',
        'organization_name': org.name,
        'facility_admin_id': connection.facility.admin_id if connection else None,
        'integration_email': connection.integration_email if connection else '',
        'facilities': [
            {'admin_id': f.admin_id, 'facility_name': f.facility_name,
             'connected': f.integration_platform == 'therapy_pms'}
            for f in facilities
        ],
    }


@router.patch('/settings/integrations', response=OrganizationIntegrationSettingsSchema)
def update_integration_settings(request, data: OrganizationIntegrationSettingsUpdate, facility_admin_id: int | None = None):
    _require_manager(request)
    org = _integration_connection(request, facility_admin_id)

    if data.integration_platform is not None:
        valid_platforms = {choice.value for choice in Organization.IntegrationPlatform}
        if data.integration_platform not in valid_platforms:
            raise HttpError(400, 'Unsupported practice management platform')
        org.integration_platform = data.integration_platform
        org.save(update_fields=['integration_platform', 'updated_at'])

    return get_integration_settings(request, org.facility.admin_id)


@router.post('/settings/integrations/therapy-pms/connect', response=OrganizationIntegrationSettingsSchema)
def connect_therapy_pms(request, data: TherapyPmsConnectRequest, facility_admin_id: int | None = None):
    """Verify TherapyPMS admin credentials, bind this org to that practice, and
    store encrypted credentials for later Pull sync. The password is never
    returned to the client."""
    _require_manager(request)
    from apps.integrations.tpms_pull import connect_facility_from_login

    if not data.email.strip() or not data.password:
        raise HttpError(400, 'Email and password are required.')

    org = _organization(request)
    connection = connect_facility_from_login(org, data.email.strip(), data.password, facility_admin_id)
    return get_integration_settings(request, connection.facility.admin_id)


@router.post('/settings/integrations/disconnect', response={204: None})
def disconnect_integration(request, facility_admin_id: int | None = None):
    _require_manager(request)
    from apps.integrations.tpms_pull import clear_therapy_pms_connection

    org = _integration_connection(request, facility_admin_id)
    clear_therapy_pms_connection(org)
    return 204, None


def _dispatch_pull_job(request, job_type: str, params: dict, facility_admin_id: int | None = None) -> PullJob:
    """Creates a PullJob row and hands it to Celery. A pull (appointments
    especially, which can span years) used to run inline in this request and
    could exceed the web server's own timeout; it now returns immediately
    and the caller polls /pull-jobs/{id} for progress."""
    from apps.integrations.tasks import run_pull_job

    from apps.integrations.tpms_pull import _require_connected_org
    org = _integration_connection(request, facility_admin_id)
    _require_connected_org(org)
    params = {**params, 'facility_admin_id': org.facility.admin_id}
    job = PullJob.objects.create(
        job_type=job_type,
        params=params,
        created_by=request.user,
    )
    try:
        run_pull_job.delay(job_id=job.id, org_id=org.id)
    except Exception as exc:
        logger.exception('Failed to dispatch pull job %s to Celery', job.id)
        job.status = PullJob.Status.FAILED
        job.error_message = f'Failed to dispatch background task: {exc}'
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'error_message', 'finished_at'])
    return job


def _expire_stale_job(job: PullJob) -> PullJob:
    """Marks pending jobs older than 3 minutes or running jobs older than 30 minutes as failed
    so the UI is never permanently trapped in an endless polling loop if a worker is offline."""
    now = timezone.now()
    if job.status == PullJob.Status.PENDING and (now - job.created_at) > timedelta(minutes=3):
        job.status = PullJob.Status.FAILED
        job.error_message = 'Job timed out before worker picked it up. Please ensure Celery worker is running and try again.'
        job.finished_at = now
        job.save(update_fields=['status', 'error_message', 'finished_at'])
    elif job.status == PullJob.Status.RUNNING and job.started_at and (now - job.started_at) > timedelta(minutes=30):
        job.status = PullJob.Status.FAILED
        job.error_message = 'Job timed out during execution.'
        job.finished_at = now
        job.save(update_fields=['status', 'error_message', 'finished_at'])
    return job


@router.post(
    '/settings/integrations/therapy-pms/pull/clients',
    response={202: PullJobSchema},
)
def pull_therapy_pms_clients(request, facility_admin_id: int | None = None):
    """Queue a background pull of clients from TherapyPMS into this org."""
    _require_manager(request)
    return 202, _dispatch_pull_job(request, PullJob.JobType.CLIENTS, {}, facility_admin_id)


@router.post(
    '/settings/integrations/therapy-pms/pull/providers',
    response={202: PullJobSchema},
)
def pull_therapy_pms_providers(request, facility_admin_id: int | None = None):
    """Queue a background pull of providers from TherapyPMS into this org's users."""
    _require_manager(request)
    return 202, _dispatch_pull_job(request, PullJob.JobType.PROVIDERS, {}, facility_admin_id)


@router.post(
    '/settings/integrations/therapy-pms/pull/appointments',
    response={202: PullJobSchema},
)
def pull_therapy_pms_appointments(request, data: TherapyPmsPullAppointmentsRequest, facility_admin_id: int | None = None):
    """Queue a background pull of appointments/sessions for a date range.
    Chunked month-by-month inside the task, so a multi-year backfill (e.g.
    2022 to today) no longer needs to finish inside one HTTP request."""
    _require_manager(request)
    if data.to_date < data.from_date:
        raise HttpError(400, 'to_date must be on or after from_date')
    params = {
        'from_date': data.from_date.isoformat(),
        'to_date': data.to_date.isoformat(),
        'patient_ids': data.patient_ids,
        'staff_ids': data.staff_ids,
    }
    return 202, _dispatch_pull_job(request, PullJob.JobType.APPOINTMENTS, params, facility_admin_id)


@router.get(
    '/settings/integrations/pull-jobs/{job_id}',
    response=PullJobSchema,
)
def get_pull_job(request, job_id: int):
    """Poll a pull job's progress/result — the admin UI calls this instead of
    blocking on the pull itself."""
    _require_manager(request)
    try:
        job = PullJob.objects.get(id=job_id)
        return _expire_stale_job(job)
    except PullJob.DoesNotExist:
        raise HttpError(404, 'Pull job not found')


@router.get(
    '/settings/integrations/pull-jobs',
    response=list[PullJobSchema],
)
def list_pull_jobs(request, job_type: str | None = None, limit: int = 5, facility_admin_id: int | None = None):
    """Most recent pull jobs for this org, optionally filtered by type — lets
    the admin UI resume showing an in-progress job after a page reload."""
    _require_manager(request)
    qs = PullJob.objects.all()
    if facility_admin_id is not None:
        _integration_connection(request, facility_admin_id)
        qs = qs.filter(params__facility_admin_id=facility_admin_id)
    if job_type:
        qs = qs.filter(job_type=job_type)
    jobs = list(qs[:max(1, min(limit, 20))])
    return [_expire_stale_job(j) for j in jobs]



# ---------------------------------------------------------------------------
# Superadmin: partner API keys for any organization
#
# Partner API keys are a superadmin-only concern — there is no
# practice-admin-facing key endpoint. These let a platform superadmin mint /
# list / revoke a key for any organization, then hand the one-time raw key
# to the partner.
# ---------------------------------------------------------------------------

def _facility_name_map(admin_ids: set[int]) -> dict[int, str]:
    """admin_id -> facility_name for the given TPMS practice admin IDs."""
    if not admin_ids:
        return {}
    return dict(
        OrganizationTpmsAdminId.objects
        .filter(admin_id__in=admin_ids)
        .values_list('admin_id', 'facility_name')
    )


def _serialize_superadmin_api_key(key: APIKey, facility_names: dict[int, str] | None = None) -> dict:
    facility_names = facility_names or {}
    return {
        'id': key.id,
        'name': key.name,
        'key_prefix': key.key_prefix,
        'organization_id': key.organization_id,
        'organization_name': key.organization.name if key.organization_id else '',
        'external_admin_id': key.external_admin_id,
        'tpms_facility_name': (
            facility_names.get(key.external_admin_id) or None
            if key.external_admin_id is not None else None
        ),
        'can_write': key.can_write,
        'is_active': key.is_active,
        'expires_at': key.expires_at,
        'last_used_at': key.last_used_at,
        'created_at': key.created_at,
    }


@router.get('/superadmin/api-keys', response=list[SuperadminAPIKeySchema])
def list_superadmin_api_keys(request, organization_id: int | None = None):
    _require_superadmin(request)
    qs = APIKey.objects.select_related('organization').order_by('-created_at')
    if organization_id is not None:
        qs = qs.filter(organization_id=organization_id)
    keys = list(qs)
    facility_names = _facility_name_map({k.external_admin_id for k in keys if k.external_admin_id is not None})
    return [_serialize_superadmin_api_key(key, facility_names) for key in keys]


@router.post('/superadmin/api-keys', response={201: SuperadminAPIKeyCreatedSchema})
def create_superadmin_api_key(request, data: SuperadminAPIKeyCreate):
    _require_superadmin(request)
    try:
        org = Organization.objects.get(id=data.organization_id)
    except Organization.DoesNotExist:
        raise HttpError(404, 'Organization not found')

    # Own the key with an existing admin of the target org where one exists,
    # so it has a sensible created_by; fall back to the superadmin.
    owner = (
        User.objects.filter(organization=org, role=User.Role.ADMIN, is_active=True)
        .order_by('id')
        .first()
        or request.user
    )

    # The key's practice scope must be one of THIS org's mapped TPMS practices
    # (OrganizationTpmsAdminId). A caller-supplied id that isn't mapped here is
    # rejected; an inherited one is only used if it is actually mapped.
    org_admin_ids = set(
        OrganizationTpmsAdminId.objects.filter(organization=org).values_list('admin_id', flat=True)
    )
    external_admin_id = data.external_admin_id
    if external_admin_id is not None:
        if org_admin_ids and external_admin_id not in org_admin_ids:
            raise HttpError(
                400,
                f'TPMS practice admin ID {external_admin_id} is not mapped to "{org.name}". '
                f'Mapped: {sorted(org_admin_ids) or "none"}.',
            )
    elif owner is not None and owner.external_admin_id in org_admin_ids:
        external_admin_id = owner.external_admin_id

    key, raw = APIKey.generate(
        name=data.name,
        created_by=owner,
        organization_id=org.id,
        external_admin_id=external_admin_id,
        can_write=data.can_write,
        expires_at=data.expires_at,
    )
    payload = _serialize_superadmin_api_key(
        key, _facility_name_map({external_admin_id} if external_admin_id is not None else set()),
    )
    payload['raw_key'] = raw
    return 201, payload


@router.delete('/superadmin/api-keys/{key_id}', response={204: None})
def revoke_superadmin_api_key(request, key_id: int):
    _require_superadmin(request)
    try:
        key = APIKey.objects.get(id=key_id)
    except APIKey.DoesNotExist:
        raise HttpError(404, 'API key not found')
    key.is_active = False
    key.save(update_fields=['is_active'])
    if key.service_user_id:
        User.objects.filter(id=key.service_user_id).update(is_active=False)
    return 204, None

# ---------------------------------------------------------------------------
# Superadmin: create login accounts (Administrator/Supervisor/Staff) for any
# organization. Distinct from accounts.api.create_user, which scopes to the
# calling admin's own organization and caps role at the caller's own rank —
# a superadmin instead picks the target org explicitly and may grant any of
# these three roles regardless of their own (superuser accounts have no
# `role`/organization of their own to rank against).
# ---------------------------------------------------------------------------

_SUPERADMIN_ASSIGNABLE_ROLES = {User.Role.ADMIN, User.Role.SUPERVISOR, User.Role.STAFF}


def _serialize_superadmin_user(user: User, facility_names: dict[int, str] | None = None) -> dict:
    facility_names = facility_names or {}
    return {
        'id': user.id,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'full_name': user.full_name,
        'role': user.role,
        'is_active': user.is_active,
        'organization_id': user.organization_id,
        'organization_name': user.organization.name if user.organization_id else '',
        'external_admin_id': user.external_admin_id,
        'tpms_facility_name': (
            facility_names.get(user.external_admin_id) or None
            if user.external_admin_id is not None else None
        ),
        'created_at': user.created_at,
    }


@router.get('/superadmin/users', response=list[SuperadminUserSchema])
def list_superadmin_users(request, organization_id: int | None = None, external_admin_id: int | None = None, role: str | None = None):
    _require_superadmin(request)
    from apps.accounts.models import User as _User
    valid_roles = {c[0] for c in _User.Role.choices}
    if role and role in valid_roles:
        qs = (
            User.objects.filter(role=role)
            .select_related('organization')
            .order_by('-created_at')
        )
    else:
        qs = (
            User.objects.filter(role__in=_SUPERADMIN_ASSIGNABLE_ROLES)
            .select_related('organization')
            .order_by('-created_at')
        )
    if external_admin_id is not None:
        qs = qs.filter(external_admin_id=external_admin_id)
    elif organization_id is not None:
        qs = qs.filter(organization_id=organization_id)
    users = list(qs)
    facility_names = _facility_name_map({u.external_admin_id for u in users if u.external_admin_id is not None})
    return [_serialize_superadmin_user(u, facility_names) for u in users]


@router.post('/superadmin/users', response={201: SuperadminUserSchema})
def create_superadmin_user(request, data: SuperadminUserCreate):
    _require_superadmin(request)
    try:
        org = Organization.objects.get(id=data.organization_id)
    except Organization.DoesNotExist:
        raise HttpError(404, 'Organization not found')

    if data.role not in _SUPERADMIN_ASSIGNABLE_ROLES:
        raise HttpError(400, 'Role must be one of: admin, supervisor, staff')
    if len(data.password) < 8:
        raise HttpError(400, 'Password must be at least 8 characters')
    if User.objects.filter(email__iexact=data.email).exists():
        raise HttpError(400, 'A user with this email already exists')

    # Local-password login binds a TPMS-linked org's users by external_admin_id
    # (see accounts.api._staff_local_auth) — a user created without one could
    # never log in for such an org, so this is required (not inferred) here,
    # per an explicit org+admin_id pairing rather than org alone. Native
    # (non-TPMS) orgs have no mapped practices and don't need one at all.
    org_admin_ids = set(
        OrganizationTpmsAdminId.objects.filter(organization=org).values_list('admin_id', flat=True)
    )
    external_admin_id = data.external_admin_id
    if org_admin_ids:
        if external_admin_id is None:
            raise HttpError(
                400,
                f'"{org.name}" has mapped TPMS practices — specify which admin ID this user belongs to: '
                f'{sorted(org_admin_ids)}.',
            )
        if external_admin_id not in org_admin_ids:
            raise HttpError(
                400,
                f'TPMS admin ID {external_admin_id} is not mapped to "{org.name}". '
                f'Mapped: {sorted(org_admin_ids)}.',
            )
    else:
        external_admin_id = None

    user = User.objects.create_user(
        email=data.email,
        first_name=data.first_name,
        last_name=data.last_name,
        role=data.role,
        password=data.password,
        organization=org,
        external_admin_id=external_admin_id,
    )
    return 201, _serialize_superadmin_user(
        user, _facility_name_map({external_admin_id} if external_admin_id is not None else set()),
    )


@router.patch('/superadmin/users/{user_id}/deactivate', response={204: None})
def deactivate_superadmin_user(request, user_id: int):
    _require_superadmin(request)
    updated = User.objects.filter(id=user_id, role__in=_SUPERADMIN_ASSIGNABLE_ROLES).update(is_active=False)
    if not updated:
        raise HttpError(404, 'User not found')
    return 204, None


@router.patch('/superadmin/users/{user_id}', response=SuperadminUserSchema)
def update_superadmin_user(request, user_id: int, data: SuperadminUserUpdate):
    _require_superadmin(request)
    try:
        user = User.objects.select_related('organization').get(
            id=user_id, role__in=_SUPERADMIN_ASSIGNABLE_ROLES
        )
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')
    if data.email is not None:
        email = data.email.strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=user_id).exists():
            raise HttpError(400, 'A user with this email already exists.')
        user.email = email
    if data.first_name is not None:
        user.first_name = data.first_name.strip()
    if data.last_name is not None:
        user.last_name = data.last_name.strip()
    if data.role is not None:
        if data.role not in _SUPERADMIN_ASSIGNABLE_ROLES:
            raise HttpError(400, 'Invalid role.')
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:
        if len(data.password) < 8:
            raise HttpError(400, 'Password must be at least 8 characters.')
        user.set_password(data.password)
    user.save()
    facility_names = _facility_name_map(
        {user.external_admin_id} if user.external_admin_id is not None else set()
    )
    return _serialize_superadmin_user(user, facility_names)


# ---------------------------------------------------------------------------
# Superadmin: User Impersonation
#
# Only platform superadmins may impersonate. Impersonation issues a new
# JWT pair with the target user's identity but with the original admin's ID
# preserved in the payload. The original admin's session is stored in Redis
# so it can be restored without re-authentication. Superadmins cannot
# impersonate other superadmins. Impersonation is fully audit-logged.
# ---------------------------------------------------------------------------

_IMPERSONATION_SESSION_TTL = 4 * 3600  # 4 hours max


def _impersonation_redis_key(session_id: str) -> str:
    return f'dcm:impersonation:admin_session:{session_id}'


def _store_admin_session(session_id: str, admin_id: int, admin_access: str, admin_refresh: str, tenant_id: int) -> None:
    import json
    from apps.accounts.auth import _redis
    _redis().setex(
        _impersonation_redis_key(session_id),
        _IMPERSONATION_SESSION_TTL,
        json.dumps({
            'admin_id': admin_id,
            'access_token': admin_access,
            'refresh_token': admin_refresh,
            'tenant_id': tenant_id,
        }),
    )


def _retrieve_admin_session(session_id: str) -> dict | None:
    import json
    from apps.accounts.auth import _redis
    raw = _redis().get(_impersonation_redis_key(session_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _delete_admin_session(session_id: str) -> None:
    from apps.accounts.auth import _redis
    _redis().delete(_impersonation_redis_key(session_id))


def _client_ip(request) -> str | None:
    import ipaddress
    meta = getattr(request, 'META', {})
    raw = meta.get('HTTP_X_FORWARDED_FOR', meta.get('REMOTE_ADDR', ''))
    candidate = raw.split(',')[0].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


@router.post('/superadmin/impersonate/stop', response=dict)
def stop_impersonation_route(request):
    return _stop_impersonation(request)


@router.post('/superadmin/impersonate/{target_user_id}', response=dict)
def start_impersonation(request, target_user_id: int, data: ImpersonateRequest):
    """
    Begin impersonating a non-superadmin user. Issues a new JWT pair scoped
    to the target user with the original admin's ID embedded. The admin's
    current tokens are stored in Redis for restoration.
    """
    import secrets as _secrets
    from apps.accounts.auth import (
        create_access_token, create_refresh_token, decode_token,
        session_start_from_payload,
    )
    from apps.accounts.models import ImpersonationLog

    _require_superadmin(request)

    # Prevent nested impersonation
    payload = getattr(request, '_jwt_payload', {})
    if payload.get('impersonating_admin_id'):
        raise HttpError(400, 'Cannot impersonate while already impersonating another user.')

    try:
        target = User.objects.get(id=target_user_id)
    except User.DoesNotExist:
        raise HttpError(404, 'User not found')

    if target.is_superuser:
        raise HttpError(403, 'Cannot impersonate another superadmin.')
    if not target.is_active:
        raise HttpError(400, 'Cannot impersonate an inactive user.')
    if target.role == User.Role.CAREGIVER:
        raise HttpError(400, 'Cannot impersonate caregiver accounts.')

    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise HttpError(400, 'No organization context')

    admin = request.user
    impersonation_session_id = _secrets.token_hex(16)

    # Preserve the admin's current tokens in Redis
    admin_access = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    admin_refresh_payload = payload  # we only need the session metadata
    admin_session_started_at = payload.get('session_started_at', payload.get('iat'))
    admin_session_id = payload.get('sid', '')

    # Store admin session for restoration
    _store_admin_session(
        impersonation_session_id,
        admin_id=admin.id,
        admin_access=admin_access,
        admin_refresh='http-only-session',  # gateway will supply the real refresh cookie
        tenant_id=tenant.pk,
    )

    # Create audit log entry
    log = ImpersonationLog.objects.create(
        admin=admin,
        target_user=target,
        ip_address=_client_ip(request),
        session_id=impersonation_session_id,
        reason=data.reason or '',
    )

    # Issue impersonation tokens — target user's identity, admin's ID embedded
    now = timezone.now()
    session_start = now.timestamp()
    access = create_access_token(
        target, tenant.pk,
        session_started_at=session_start,
        session_id=impersonation_session_id,
        idle_started_at=session_start,
        impersonating_admin_id=admin.id,
        impersonation_log_id=log.id,
    )
    refresh = create_refresh_token(
        target, tenant.pk,
        session_started_at=session_start,
        session_id=impersonation_session_id,
        idle_started_at=session_start,
        impersonating_admin_id=admin.id,
        impersonation_log_id=log.id,
    )

    return {
        'access_token': access,
        'refresh_token': refresh,
        'impersonation_log_id': log.id,
        'target_user': {
            'id': target.id,
            'full_name': target.full_name,
            'email': target.email,
            'role': target.role,
        },
    }


def _stop_impersonation(request):
    """
    End the current impersonation session and restore the original admin's
    tokens. The admin does not need to re-authenticate.
    """
    from apps.accounts.auth import (
        create_access_token, create_refresh_token, blocklist_token,
    )
    from apps.accounts.models import ImpersonationLog

    payload = getattr(request, '_jwt_payload', {})
    impersonating_admin_id = payload.get('impersonating_admin_id')
    if not impersonating_admin_id:
        raise HttpError(400, 'No active impersonation session.')

    impersonation_session_id = payload.get('sid', '')
    impersonation_log_id = payload.get('impersonation_log_id')

    # Revoke the impersonation token immediately
    blocklist_token(payload)

    # Retrieve and delete the stored admin session
    admin_session = _retrieve_admin_session(impersonation_session_id)
    _delete_admin_session(impersonation_session_id)

    # Update audit log
    if impersonation_log_id:
        ImpersonationLog.objects.filter(
            id=impersonation_log_id,
            status=ImpersonationLog.Status.ACTIVE,
        ).update(ended_at=timezone.now(), status=ImpersonationLog.Status.ENDED)

    if not admin_session:
        raise HttpError(401, 'Original admin session has expired. Please sign in again.')

    try:
        admin = User.objects.get(id=admin_session['admin_id'], is_active=True, is_superuser=True)
    except User.DoesNotExist:
        raise HttpError(401, 'Original admin account is no longer active.')

    tenant_id = admin_session['tenant_id']
    now = timezone.now()
    session_start = now.timestamp()
    session_id = admin_session.get('session_id') or payload.get('sid', '')

    # Issue fresh admin tokens (new session — no impersonation claims)
    access = create_access_token(
        admin, tenant_id,
        session_started_at=session_start,
        session_id=session_id,
        idle_started_at=session_start,
    )
    refresh = create_refresh_token(
        admin, tenant_id,
        session_started_at=session_start,
        session_id=session_id,
        idle_started_at=session_start,
    )

    return {
        'access_token': access,
        'refresh_token': refresh,
    }


@router.get('/superadmin/impersonation-logs', response=list[ImpersonationLogSchema])
def list_impersonation_logs(request, limit: int = 50):
    """Return recent impersonation audit logs. Superadmin only."""
    from apps.accounts.models import ImpersonationLog
    _require_superadmin(request)
    logs = (
        ImpersonationLog.objects
        .select_related('admin', 'target_user')
        .order_by('-started_at')[:max(1, min(limit, 200))]
    )
    return [
        ImpersonationLogSchema(
            id=log.id,
            admin_id=log.admin_id,
            admin_name=log.admin.full_name,
            admin_email=log.admin.email,
            target_user_id=log.target_user_id,
            target_user_name=log.target_user.full_name,
            target_user_email=log.target_user.email,
            started_at=log.started_at,
            ended_at=log.ended_at,
            ip_address=log.ip_address,
            session_id=log.session_id,
            status=log.status,
            reason=log.reason,
        )
        for log in logs
    ]
