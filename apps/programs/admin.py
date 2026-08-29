from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from shared.admin import OrganizationScopedAdminMixin
from .models import (
    Program, ProgramMaterial, Target, PromptingTemplate,
    WorkflowTemplate, MaintenanceSchedule, FadingTemplate,
    Lesson, LessonProgram, ProgramModule, ProgramSubmodule,
    TargetSubItem, TargetSubItemStatusChange,
)


class TargetInline(OrganizationScopedAdminMixin, TabularInline):
    model = Target
    extra = 0
    fields = ['name', 'measurement_type', 'measurement', 'timer_type', 'status', 'display_order', 'is_visible_to_staff']
    ordering = ['display_order']


class TargetSubItemInline(OrganizationScopedAdminMixin, TabularInline):
    model = TargetSubItem
    extra = 0
    fields = ['label', 'key', 'status', 'measurement_type', 'measurement', 'prompting_template', 'workflow_template', 'display_order']
    ordering = ['display_order']


class ProgramMaterialInline(OrganizationScopedAdminMixin, TabularInline):
    model = ProgramMaterial
    extra = 0
    fields = ['title', 'material_type', 'file', 'file_size', 'created_at']
    readonly_fields = ['file_size', 'created_at']


@admin.register(Program)
class ProgramAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'external_client_id', 'category', 'status', 'treatment_area', 'created_at']
    list_filter = ['category', 'status']
    search_fields = ['name', 'treatment_area']
    inlines = [TargetInline, ProgramMaterialInline]
    readonly_fields = ['created_at', 'updated_at', 'archived_at']


@admin.register(Target)
class TargetAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'program', 'measurement_type', 'measurement', 'timer_type', 'status', 'sub_item_progression', 'display_order', 'is_visible_to_staff']
    list_filter = ['status', 'measurement_type', 'measurement', 'sub_item_progression']
    search_fields = ['name', 'sd_text']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [TargetSubItemInline]


@admin.register(TargetSubItem)
class TargetSubItemAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['label', 'target', 'status', 'measurement_type', 'measurement', 'display_order']
    list_filter = ['status', 'measurement_type', 'measurement']
    search_fields = ['label', 'target__name']


@admin.register(TargetSubItemStatusChange)
class TargetSubItemStatusChangeAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['sub_item', 'from_status', 'to_status', 'trigger', 'session_run_id', 'created_at']
    list_filter = ['trigger', 'to_status']
    readonly_fields = ['created_at']


@admin.register(ProgramMaterial)
class ProgramMaterialAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['title', 'program', 'material_type', 'file_size', 'created_at']
    list_filter = ['material_type']
    search_fields = ['title', 'program__name']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(PromptingTemplate)
class PromptingTemplateAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'is_org_default', 'created_at']
    list_filter = ['is_org_default']
    search_fields = ['name']


@admin.register(WorkflowTemplate)
class WorkflowTemplateAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'is_org_default', 'created_at']
    list_filter = ['is_org_default']
    search_fields = ['name']


@admin.register(FadingTemplate)
class FadingTemplateAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'is_org_default', 'created_at']
    list_filter = ['is_org_default']
    search_fields = ['name']


@admin.register(MaintenanceSchedule)
class MaintenanceScheduleAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'interval_type', 'interval_value', 'episodes', 'success_threshold_pct', 'on_failure', 'is_org_default', 'created_at']
    list_filter = ['interval_type', 'on_failure', 'is_org_default']
    search_fields = ['name']


class LessonProgramInline(OrganizationScopedAdminMixin, TabularInline):
    model = LessonProgram
    extra = 0
    ordering = ['display_order']


@admin.register(Lesson)
class LessonAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'external_client_id', 'lesson_type', 'is_active', 'created_at']
    list_filter = ['lesson_type', 'is_active']
    search_fields = ['name']
    inlines = [LessonProgramInline]
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ProgramModule)
class ProgramModuleAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'program', 'display_order', 'created_at']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ProgramSubmodule)
class ProgramSubmoduleAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'module', 'display_order', 'created_at']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']
