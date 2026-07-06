from django.db import models
from django.conf import settings

from .tenancy import AllOrganizationsManager, TenantManager, current_org_id


class OrganizationScopedMixin(models.Model):
    """
    Row-level tenant isolation: every tenant-scoped model gets an
    `organization` FK plus a manager (`objects`) that auto-filters every
    query to the current tenant (see shared/tenancy.py). `all_organizations`
    is the explicit, opt-in bypass for legitimate cross-tenant code
    (Django admin, the TPMS sync command) — it is never the default.

    Subclasses whose organization is unambiguously derivable from a parent
    FK (e.g. Target.program) should override `_derive_organization_id()` to
    return that parent's organization_id instead of relying on the ambient
    context — this makes a cross-organization mismatch structurally
    impossible for that field rather than merely checked. Every subclass
    should also set `_org_scoped_fk_fields` to the list of FK field names
    that must belong to the same organization as this row; `save()`
    validates all of them.
    """
    organization = models.ForeignKey(
        'tenants.Organization',
        on_delete=models.CASCADE,
        db_index=True,
        # TODO(M2): drop null=True. Temporarily nullable only so the M1 pilot
        # migration doesn't fail against dev schemas that already have rows
        # with no organization value — M2 deletes all migrations and
        # regenerates fresh with this required from each model's first
        # migration, since dev data is disposable (confirmed with the user).
        null=True,
        blank=True,
    )

    objects = TenantManager()
    all_organizations = AllOrganizationsManager()

    _org_scoped_fk_fields: tuple[str, ...] = ()

    class Meta:
        abstract = True
        # Without this, forward FK access (e.g. target.prompting_template)
        # uses Django's plain unfiltered Manager instead of TenantManager,
        # so a cross-org FK id (however it got written) would silently
        # return the other org's row instead of raising DoesNotExist.
        base_manager_name = 'objects'

    def _derive_organization_id(self) -> int | None:
        """Override in subclasses whose organization comes from a parent FK
        rather than the ambient tenant context. Return None to fall back to
        current_org_id()."""
        return None

    def _validate_cross_org_fks(self) -> None:
        from .tenancy import CrossOrganizationReferenceError

        for field_name in self._org_scoped_fk_fields:
            related = getattr(self, field_name, None)
            if related is None:
                continue
            related_org_id = related.organization_id
            if related_org_id != self.organization_id:
                raise CrossOrganizationReferenceError(
                    f'{self.__class__.__name__}.{field_name} (org={related_org_id}) '
                    f'does not belong to this row\'s organization (org={self.organization_id}).'
                )

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            derived = self._derive_organization_id()
            self.organization_id = derived if derived is not None else current_org_id()
        self._validate_cross_org_fks()
        super().save(*args, **kwargs)


class TenantAwareModel(OrganizationScopedMixin):
    """
    Base for all tenant-scoped clinical data.
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        db_constraint=False,  # User lives in a shared table; not FK-constrained to any one organization
    )

    class Meta:
        abstract = True
