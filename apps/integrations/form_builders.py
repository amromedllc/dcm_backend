"""Facility form-builder policy, evaluated against the authenticated request tenant."""
from ninja.errors import HttpError

from apps.tenants.models import OrganizationTpmsAdminId


def form_builder_mode(request) -> str | None:
    facility_id = getattr(request.user, 'external_admin_id', None)
    if facility_id is None:
        # Preserve the existing new-builder flow for native accounts.
        return OrganizationTpmsAdminId.FormBuilderMode.NEW
    tenant_id = getattr(getattr(request, 'tenant', None), 'pk', None)
    if tenant_id is None:
        return None
    # TPMS-linked users may have no organization FK. JWT auth has already
    # verified request.tenant against the session's org_id; never infer another org.
    return OrganizationTpmsAdminId.objects.filter(
        organization_id=tenant_id, admin_id=facility_id,
    ).values_list('form_builder_mode', flat=True).first()


def require_form_builder(request, builder: str) -> None:
    mode = form_builder_mode(request)
    if mode not in (builder, OrganizationTpmsAdminId.FormBuilderMode.BOTH):
        raise HttpError(403, 'This form builder is not enabled for your facility.')
