from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import CentralProgram, CentralTarget


class _SuperuserOnlyAdminMixin:
    """Central Library content is platform-owned, not tied to any
    organization — authoring is restricted to superusers rather than any
    org-scoped permission."""

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


class CentralTargetInline(_SuperuserOnlyAdminMixin, TabularInline):
    model = CentralTarget
    extra = 0
    fields = ['name', 'measurement_type', 'sd_text', 'display_order', 'is_visible_to_staff']
    ordering = ['display_order']


@admin.register(CentralProgram)
class CentralProgramAdmin(_SuperuserOnlyAdminMixin, ModelAdmin):
    list_display = ['name', 'category', 'phase', 'status', 'treatment_area', 'display_order', 'updated_at']
    list_filter = ['category', 'status']
    search_fields = ['name', 'treatment_area']
    inlines = [CentralTargetInline]
    readonly_fields = ['created_at', 'updated_at', 'created_by']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(CentralTarget)
class CentralTargetAdmin(_SuperuserOnlyAdminMixin, ModelAdmin):
    list_display = ['name', 'program', 'measurement_type', 'display_order', 'is_visible_to_staff']
    list_filter = ['measurement_type']
    search_fields = ['name', 'sd_text']
