from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from shared.admin import OrganizationScopedAdminMixin
from .models import Client, ClientStaffAssignment


class StaffAssignmentInline(OrganizationScopedAdminMixin, TabularInline):
    model = ClientStaffAssignment
    extra = 0
    fields = ['user', 'is_primary', 'is_active', 'assigned_at']
    readonly_fields = ['assigned_at']


@admin.register(Client)
class ClientAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['full_name', 'external_id', 'status', 'date_of_birth', 'intake_date']
    list_filter = ['status']
    search_fields = ['first_name', 'last_name', 'preferred_name', 'external_id']
    inlines = [StaffAssignmentInline]
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Identity', {'fields': ('first_name', 'last_name', 'preferred_name', 'external_id', 'date_of_birth')}),
        ('Status', {'fields': ('status', 'intake_date', 'discharge_date')}),
        ('Notes', {'fields': ('internal_notes',)}),
        ('Audit', {'fields': ('created_by', 'created_at', 'updated_at')}),
    )
