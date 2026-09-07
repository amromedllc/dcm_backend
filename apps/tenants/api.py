from ninja import Router
from ninja.errors import HttpError

from .models import Organization
from .schemas import (
    OrganizationAuthenticationSettingsSchema,
    OrganizationAuthenticationSettingsUpdate,
)

router = Router()


def _require_manager(request) -> None:
    if not request.user.has_role('admin', 'supervisor'):
        raise HttpError(403, 'Manager access required')


def _organization(request) -> Organization:
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise HttpError(400, 'No organization context')
    return tenant


@router.get('/settings/authentication', response=OrganizationAuthenticationSettingsSchema)
def get_authentication_settings(request):
    _require_manager(request)
    org = _organization(request)
    return {
        'automatic_logout_enabled': org.automatic_logout_enabled,
        'automatic_logout_minutes': org.automatic_logout_minutes,
    }


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
