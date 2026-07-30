from django.contrib import admin
from unfold.admin import ModelAdmin
from shared.admin import OrganizationScopedAdminMixin
from .models import BehaviorInterventionPlan


@admin.register(BehaviorInterventionPlan)
class BehaviorInterventionPlanAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['id', 'program', 'version_number', 'status', 'effective_date', 'activated_at']
    list_filter = ['status', 'hypothesized_function']
    search_fields = ['target_behavior_name']
    readonly_fields = [
        'submitted_at', 'activated_by_id', 'activated_by_name', 'activated_at',
        'rejected_by_id', 'rejected_at', 'archived_by_id', 'archived_at',
        'created_at', 'updated_at',
    ]
    fieldsets = (
        ('Identity', {'fields': ('program', 'version_number', 'previous_version', 'status', 'title', 'author', 'author_name', 'effective_date', 'review_due_date')}),
        ('Clinical Content', {'fields': (
            'target_behavior_name', 'target_behavior_definition', 'baseline_summary',
            'hypothesized_function', 'function_hypothesis_statement',
            'antecedent_strategies', 'replacement_behaviors',
            'teaching_procedures', 'reinforcement_strategies', 'response_procedures',
            'data_collection_plan', 'generalization_plan',
        )}),
        ('Crisis & Safety', {'fields': ('crisis_plan', 'safety_precautions')}),
        ('Workflow', {'fields': (
            'submitted_at', 'activated_by_id', 'activated_by_name', 'activated_at',
            'rejected_by_id', 'rejected_at', 'rejection_reason',
            'archived_by_id', 'archived_at', 'archive_reason',
        )}),
        ('Audit', {'fields': ('created_by', 'created_at', 'updated_at')}),
    )
