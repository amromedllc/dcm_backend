from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import (
    Program, Target, PromptingTemplate, MasteryTemplate,
    WorkflowTemplate, MaintenanceSchedule,
    Lesson, LessonProgram,
)


class TargetInline(TabularInline):
    model = Target
    extra = 0
    fields = ['name', 'measurement_type', 'status', 'display_order', 'is_visible_to_staff']
    ordering = ['display_order']


@admin.register(Program)
class ProgramAdmin(ModelAdmin):
    list_display = ['name', 'external_client_id', 'category', 'status', 'treatment_area', 'created_at']
    list_filter = ['category', 'status']
    search_fields = ['name', 'treatment_area']
    inlines = [TargetInline]
    readonly_fields = ['created_at', 'updated_at', 'archived_at']


@admin.register(Target)
class TargetAdmin(ModelAdmin):
    list_display = ['name', 'program', 'measurement_type', 'status', 'display_order', 'is_visible_to_staff']
    list_filter = ['status', 'measurement_type']
    search_fields = ['name', 'sd_text']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(PromptingTemplate)
class PromptingTemplateAdmin(ModelAdmin):
    list_display = ['name', 'is_org_default', 'created_at']
    list_filter = ['is_org_default']
    search_fields = ['name']


@admin.register(MasteryTemplate)
class MasteryTemplateAdmin(ModelAdmin):
    list_display = ['name', 'is_org_default', 'created_at']
    list_filter = ['is_org_default']
    search_fields = ['name']


@admin.register(WorkflowTemplate)
class WorkflowTemplateAdmin(ModelAdmin):
    list_display = ['name', 'is_org_default', 'created_at']
    list_filter = ['is_org_default']
    search_fields = ['name']


@admin.register(MaintenanceSchedule)
class MaintenanceScheduleAdmin(ModelAdmin):
    list_display = ['name', 'interval_type', 'interval_value', 'episodes', 'success_threshold_pct', 'on_failure', 'is_org_default', 'created_at']
    list_filter = ['interval_type', 'on_failure', 'is_org_default']
    search_fields = ['name']


class LessonProgramInline(TabularInline):
    model = LessonProgram
    extra = 0
    ordering = ['display_order']


@admin.register(Lesson)
class LessonAdmin(ModelAdmin):
    list_display = ['name', 'external_client_id', 'lesson_type', 'is_active', 'created_at']
    list_filter = ['lesson_type', 'is_active']
    search_fields = ['name']
    inlines = [LessonProgramInline]
    readonly_fields = ['created_at', 'updated_at']
