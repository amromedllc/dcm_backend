from ninja.errors import HttpError

from .models import RolePermission, User


def resolve_permission_organization(request):
    """Facility (Organization) that owns role-permission rows for this request.

    Privileges are facility-scoped. Prefer the login tenant from the JWT so
    TPMS users (who often have ``user.organization=None``) still read/write
    the matrix for the facility they logged into.
    """
    from apps.tenants.models import Organization

    payload = getattr(request, '_jwt_payload', None) or {}
    org_id = payload.get('org_id')
    if org_id not in (None, '', 0, '0'):
        try:
            return Organization.objects.get(pk=int(org_id))
        except (Organization.DoesNotExist, TypeError, ValueError):
            pass

    tenant = getattr(request, 'tenant', None)
    if tenant is not None:
        return tenant

    user = getattr(request, 'user', None)
    org = getattr(user, 'organization', None)
    if org is not None:
        return org

    raise HttpError(400, 'No facility context for permissions')


PERMISSION_DEFAULTS: dict[str, dict[str, bool]] = {
    User.Role.ADMIN: {
        # Dashboard
        'dashboard': True,
        # Clients
        'clients_view': True,
        'clients_create': True,
        'clients_edit': True,
        'clients_delete': True,
        # Client sub-pages
        'client_overview': True,
        'client_sessions': True,
        'client_notes': True,
        'client_programs': True,
        'client_programs_create': True,
        'client_programs_edit': True,
        'client_programs_delete': True,
        'client_history': True,
        'client_progress': True,
        'client_report': True,
        # Appointments
        'appointments_create': True,
        'appointments_edit': True,
        # Sessions
        'sessions_view': True,
        'sessions_create': True,
        'sessions_edit': True,
        'sessions_delete': True,
        'session_start': True,
        'session_approve': True,
        # Notes
        'notes_view': True,
        'notes_create': True,
        'notes_edit': True,
        'notes_delete': True,
        'note_submit': True,
        'note_approve': True,
        # Review queue
        'review_queue_view': True,
        # Templates
        'templates_view': True,
        'templates_create': True,
        'templates_edit': True,
        'templates_delete': True,
        # Org programs
        'org_programs_view': True,
        'org_programs_create': True,
        'org_programs_edit': True,
        'org_programs_delete': True,
        # Exports
        'exports_view': True,
        # Settings
        'settings_view': True,
        'settings_treatment_areas_view': True,
        'settings_treatment_areas_create': True,
        'settings_treatment_areas_edit': True,
        'settings_treatment_areas_delete': True,
        'settings_prompting_templates_view': True,
        'settings_prompting_templates_create': True,
        'settings_prompting_templates_edit': True,
        'settings_prompting_templates_delete': True,
        'settings_fading_templates_view': True,
        'settings_fading_templates_create': True,
        'settings_fading_templates_edit': True,
        'settings_fading_templates_delete': True,
        'settings_maintenance_schedules_view': True,
        'settings_maintenance_schedules_create': True,
        'settings_maintenance_schedules_edit': True,
        'settings_maintenance_schedules_delete': True,
        'settings_workflows_view': True,
        'settings_workflows_create': True,
        'settings_workflows_edit': True,
        'settings_workflows_delete': True,
        'settings_tags_view': True,
        'settings_tags_create': True,
        'settings_tags_edit': True,
        'settings_tags_delete': True,
        'settings_statuses_view': True,
        'settings_statuses_create': True,
        'settings_statuses_edit': True,
        'settings_statuses_delete': True,
        'settings_data_fields_view': True,
        'settings_data_fields_create': True,
        'settings_data_fields_edit': True,
        'settings_data_fields_delete': True,
        # Admin
        'admin_users_view': True,
        'admin_users_edit': True,
        'admin_privileges': True,
    },
    User.Role.SUPERVISOR: {
        # Dashboard
        'dashboard': True,
        # Clients
        'clients_view': True,
        'clients_create': True,
        'clients_edit': True,
        'clients_delete': False,
        # Client sub-pages
        'client_overview': True,
        'client_sessions': True,
        'client_notes': True,
        'client_programs': True,
        'client_programs_create': True,
        'client_programs_edit': True,
        'client_programs_delete': True,
        'client_history': True,
        'client_progress': True,
        'client_report': True,
        # Appointments
        'appointments_create': True,
        'appointments_edit': True,
        # Sessions
        'sessions_view': True,
        'sessions_create': True,
        'sessions_edit': True,
        'sessions_delete': True,
        'session_start': True,
        'session_approve': True,
        # Notes
        'notes_view': True,
        'notes_create': True,
        'notes_edit': True,
        'notes_delete': True,
        'note_submit': True,
        'note_approve': True,
        # Review queue
        'review_queue_view': True,
        # Templates
        'templates_view': True,
        'templates_create': True,
        'templates_edit': True,
        'templates_delete': True,
        # Org programs
        'org_programs_view': False,
        'org_programs_create': False,
        'org_programs_edit': False,
        'org_programs_delete': False,
        # Exports
        'exports_view': True,
        # Settings
        'settings_view': False,
        'settings_treatment_areas_view': False,
        'settings_treatment_areas_create': False,
        'settings_treatment_areas_edit': False,
        'settings_treatment_areas_delete': False,
        'settings_prompting_templates_view': False,
        'settings_prompting_templates_create': False,
        'settings_prompting_templates_edit': False,
        'settings_prompting_templates_delete': False,
        'settings_fading_templates_view': False,
        'settings_fading_templates_create': False,
        'settings_fading_templates_edit': False,
        'settings_fading_templates_delete': False,
        'settings_maintenance_schedules_view': False,
        'settings_maintenance_schedules_create': False,
        'settings_maintenance_schedules_edit': False,
        'settings_maintenance_schedules_delete': False,
        'settings_workflows_view': False,
        'settings_workflows_create': False,
        'settings_workflows_edit': False,
        'settings_workflows_delete': False,
        'settings_tags_view': False,
        'settings_tags_create': False,
        'settings_tags_edit': False,
        'settings_tags_delete': False,
        'settings_statuses_view': False,
        'settings_statuses_create': False,
        'settings_statuses_edit': False,
        'settings_statuses_delete': False,
        'settings_data_fields_view': False,
        'settings_data_fields_create': False,
        'settings_data_fields_edit': False,
        'settings_data_fields_delete': False,
        # Admin
        'admin_users_view': True,
        'admin_users_edit': True,
        'admin_privileges': True,
    },
    User.Role.STAFF: {
        # Dashboard
        'dashboard': True,
        # Clients
        'clients_view': True,
        'clients_create': False,
        'clients_edit': False,
        'clients_delete': False,
        # Client sub-pages
        'client_overview': True,
        'client_sessions': True,
        'client_notes': True,
        'client_programs': True,
        'client_programs_create': True,
        'client_programs_edit': True,
        'client_programs_delete': False,
        'client_history': True,
        'client_progress': True,
        'client_report': False,
        # Appointments
        'appointments_create': False,
        'appointments_edit': False,
        # Sessions
        'sessions_view': True,
        'sessions_create': True,
        'sessions_edit': True,
        'sessions_delete': False,
        'session_start': True,
        'session_approve': False,
        # Notes
        'notes_view': True,
        'notes_create': True,
        'notes_edit': True,
        'notes_delete': False,
        'note_submit': True,
        'note_approve': False,
        # Review queue
        'review_queue_view': False,
        # Templates
        'templates_view': False,
        'templates_create': False,
        'templates_edit': False,
        'templates_delete': False,
        # Org programs
        'org_programs_view': False,
        'org_programs_create': False,
        'org_programs_edit': False,
        'org_programs_delete': False,
        # Exports
        'exports_view': False,
        # Settings
        'settings_view': False,
        'settings_treatment_areas_view': False,
        'settings_treatment_areas_create': False,
        'settings_treatment_areas_edit': False,
        'settings_treatment_areas_delete': False,
        'settings_prompting_templates_view': False,
        'settings_prompting_templates_create': False,
        'settings_prompting_templates_edit': False,
        'settings_prompting_templates_delete': False,
        'settings_fading_templates_view': False,
        'settings_fading_templates_create': False,
        'settings_fading_templates_edit': False,
        'settings_fading_templates_delete': False,
        'settings_maintenance_schedules_view': False,
        'settings_maintenance_schedules_create': False,
        'settings_maintenance_schedules_edit': False,
        'settings_maintenance_schedules_delete': False,
        'settings_workflows_view': False,
        'settings_workflows_create': False,
        'settings_workflows_edit': False,
        'settings_workflows_delete': False,
        'settings_tags_view': False,
        'settings_tags_create': False,
        'settings_tags_edit': False,
        'settings_tags_delete': False,
        'settings_statuses_view': False,
        'settings_statuses_create': False,
        'settings_statuses_edit': False,
        'settings_statuses_delete': False,
        'settings_data_fields_view': False,
        'settings_data_fields_create': False,
        'settings_data_fields_edit': False,
        'settings_data_fields_delete': False,
        # Admin
        'admin_users_view': False,
        'admin_users_edit': False,
        'admin_privileges': False,
    },
}

# Organisation-management keys supervisors must retain so they can grant
# access to staff even if an older privileges save turned them off.
_SUPERVISOR_ORG_MANAGEMENT_KEYS = (
    'admin_users_view',
    'admin_users_edit',
    'admin_privileges',
)


def get_user_permissions(user: User, organization) -> dict[str, bool]:
    permissions = PERMISSION_DEFAULTS.get(user.role, {}).copy()
    if organization is None:
        return _apply_role_guarantees(user.role, permissions)

    saved = (
        RolePermission.objects
        .filter(organization=organization, role=user.role)
        .values_list('permissions', flat=True)
        .first()
    )
    if isinstance(saved, dict):
        permissions.update({key: bool(value) for key, value in saved.items()})

    return _apply_role_guarantees(user.role, permissions)


def _apply_role_guarantees(role: str, permissions: dict[str, bool]) -> dict[str, bool]:
    """Force always-on permissions that the role must never lose."""
    if role == User.Role.ADMIN:
        for key, value in PERMISSION_DEFAULTS.get(User.Role.ADMIN, {}).items():
            if value:
                permissions[key] = True
    elif role == User.Role.SUPERVISOR:
        for key in _SUPERVISOR_ORG_MANAGEMENT_KEYS:
            permissions[key] = True

    # Sidebar Settings uses settings_view; grant it whenever any settings
    # subsection view permission is enabled.
    if any(
        key.startswith('settings_') and key.endswith('_view') and key != 'settings_view' and value
        for key, value in permissions.items()
    ):
        permissions['settings_view'] = True

    return permissions


def user_has_permission(user: User, organization, permission: str) -> bool:
    return get_user_permissions(user, organization).get(permission, False)


def require_permission(request, permission: str) -> None:
    organization = resolve_permission_organization(request)
    if not user_has_permission(request.user, organization, permission):
        raise HttpError(403, 'Insufficient permissions')
