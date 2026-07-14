from ninja.errors import HttpError

from .models import RolePermission, User


PERMISSION_DEFAULTS: dict[str, dict[str, bool]] = {
    User.Role.ADMIN: {
        'client_overview': True,
        'client_sessions': True,
        'client_notes': True,
        'client_programs': True,
        'client_history': True,
        'client_progress': True,
        'client_report': True,
        'dashboard': True,
        'review_queue': True,
        'templates': True,
        'settings': True,
        'settings_treatment_areas': True,
        'settings_prompting_templates': True,
        'settings_workflows': True,
        'settings_tags': True,
        'settings_statuses': True,
        'settings_data_fields': True,
        'org_programs': True,
        'admin_users': True,
        'admin_privileges': True,
        'session_start': True,
        'session_approve': True,
        'note_submit': True,
        'note_approve': True,
    },
    User.Role.SUPERVISOR: {
        'client_overview': True,
        'client_sessions': True,
        'client_notes': True,
        'client_programs': True,
        'client_history': True,
        'client_progress': True,
        'client_report': True,
        'dashboard': True,
        'review_queue': True,
        'templates': True,
        'settings': False,
        'settings_treatment_areas': False,
        'settings_prompting_templates': False,
        'settings_workflows': False,
        'settings_tags': False,
        'settings_statuses': False,
        'settings_data_fields': False,
        'org_programs': False,
        'admin_users': False,
        'admin_privileges': False,
        'session_start': True,
        'session_approve': True,
        'note_submit': True,
        'note_approve': True,
    },
    User.Role.STAFF: {
        'client_overview': True,
        'client_sessions': True,
        'client_notes': True,
        'client_programs': True,
        'client_history': True,
        'client_progress': True,
        'client_report': False,
        'dashboard': True,
        'review_queue': False,
        'templates': False,
        'settings': False,
        'settings_treatment_areas': False,
        'settings_prompting_templates': False,
        'settings_workflows': False,
        'settings_tags': False,
        'settings_statuses': False,
        'settings_data_fields': False,
        'org_programs': False,
        'admin_users': False,
        'admin_privileges': False,
        'session_start': True,
        'session_approve': False,
        'note_submit': True,
        'note_approve': False,
    },
}


def get_user_permissions(user: User, organization) -> dict[str, bool]:
    permissions = PERMISSION_DEFAULTS.get(user.role, {}).copy()
    if organization is None:
        return permissions

    saved = (
        RolePermission.objects
        .filter(organization=organization, role=user.role)
        .values_list('permissions', flat=True)
        .first()
    )
    if isinstance(saved, dict):
        permissions.update({key: bool(value) for key, value in saved.items()})

    return permissions


def user_has_permission(user: User, organization, permission: str) -> bool:
    return get_user_permissions(user, organization).get(permission, False)


def require_permission(request, permission: str) -> None:
    organization = request.user.organization or request.tenant
    if not user_has_permission(request.user, organization, permission):
        raise HttpError(403, 'Insufficient permissions')
