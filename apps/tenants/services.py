"""
Org-provisioning helpers that read platform-owned (SHARED_APPS) data and
write it into a newly created org's own tenant schema.
"""
from shared.tenancy import tenant_context


def copy_default_target_statuses_to_org(organization_id: int) -> None:
    """
    Copies the platform's DefaultTargetStatus templates (authored via Django
    Admin — see admin.py's DefaultTargetStatusAdmin, superuser-only) into the
    new org's own programs.TargetStatus table.

    This is a one-time copy, not a live link: the org's rows are its own
    from this point on, freely editable without affecting the template or
    any other org. Idempotent (get_or_create) so it's safe to re-run.

    Caller must already be inside the target org's schema (schema_context)
    — this only establishes the row-level tenant_context on top of that.
    """
    from apps.programs.models import TargetStatus
    from .models import DefaultTargetStatus

    with tenant_context(organization_id):
        for template in DefaultTargetStatus.objects.all():
            TargetStatus.objects.get_or_create(
                key=template.key,
                defaults={
                    'label': template.label,
                    'color': template.color,
                    'icon': template.icon,
                    'is_staff_visible': template.is_staff_visible,
                    'is_default': template.is_default,
                    'display_order': template.display_order,
                },
            )
