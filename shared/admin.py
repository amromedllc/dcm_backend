from .tenancy import TenantContextError


class OrganizationScopedAdminMixin:
    """
    Tenant-scoped models' default manager requires an active tenant context
    (shared/tenancy.py) and raises TenantContextError otherwise — correct for
    API requests, but Django admin can be browsed from the shared entry-point
    domain (SHOW_PUBLIC_IF_NO_TENANT_FOUND) where no tenant is resolved.
    Show an empty list there instead of a 500; browse a specific org's own
    domain to see that org's data.
    """

    def get_queryset(self, request):
        try:
            qs = self.model.objects.get_queryset()
        except TenantContextError:
            qs = self.model.all_organizations.none()
        ordering = self.get_ordering(request)
        if ordering:
            qs = qs.order_by(*ordering)
        return qs
