from django.contrib import admin
from django_tenants.admin import TenantAdminMixin
from unfold.admin import ModelAdmin, TabularInline
from .models import Organization, Domain, OrganizationTpmsAdminId, DefaultTargetStatus


class _SuperuserOnlyAdminMixin:
    """Platform-owned data, not tied to any organization — authoring is
    restricted to superusers rather than any org-scoped permission. Same
    pattern as apps.central_library.admin._SuperuserOnlyAdminMixin."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class DomainInline(TabularInline):
    model = Domain
    extra = 1


class TpmsAdminIdInline(TabularInline):
    model = OrganizationTpmsAdminId
    extra = 1


@admin.register(Organization)
class OrganizationAdmin(TenantAdminMixin, ModelAdmin):
    list_display = ['name', 'slug', 'plan', 'is_active', 'created_at']
    list_filter = ['plan', 'is_active']
    search_fields = ['name', 'slug']
    inlines = [DomainInline, TpmsAdminIdInline]


@admin.register(DefaultTargetStatus)
class DefaultTargetStatusAdmin(_SuperuserOnlyAdminMixin, ModelAdmin):
    list_display = ['label', 'key', 'is_default', 'is_staff_visible', 'display_order']
    list_editable = ['display_order']
    ordering = ['display_order', 'label']
