"""
Replaces django_tenants.middleware.main.TenantMainMiddleware. Same hostname
-> Domain -> Organization resolution, but no connection.set_tenant() schema
switch — instead it sets the shared/tenancy.py contextvar that every
tenant-scoped model's manager reads. See shared/tenancy.py's module
docstring for why the contextvar exists at all.
"""
from apps.tenants.models import Domain

from .tenancy import tenant_context


class TenantResolverMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        hostname = request.get_host().split(':')[0]
        # Ensure the attribute always exists so views can safely reference
        # `request.tenant` without needing to guard with getattr(..., None).
        request.tenant = None
        try:
            domain = Domain.objects.select_related('tenant').get(domain=hostname)
            request.tenant = domain.tenant
        except Domain.DoesNotExist:
            # Leave `request.tenant` as None when no matching domain is found.
            pass

        tenant = getattr(request, 'tenant', None)
        org_id = tenant.pk if tenant else None
        with tenant_context(org_id):
            return self.get_response(request)
