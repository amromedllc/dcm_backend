from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import Appointment, SessionRun, TrialEvent, BehaviorEvent, ABCEvent


@admin.register(Appointment)
class AppointmentAdmin(ModelAdmin):
    list_display = ['external_client_id', 'staff_id', 'start_time', 'end_time', 'status', 'source']
    list_filter = ['status', 'source']
    search_fields = ['external_id']
    readonly_fields = ['synced_at', 'created_at', 'updated_at']
    date_hierarchy = 'start_time'


class TrialEventInline(TabularInline):
    model = TrialEvent
    extra = 0
    readonly_fields = ['target_id', 'target_name', 'trial_number', 'response_score', 'prompt_level_label', 'recorded_at']
    can_delete = False


class BehaviorEventInline(TabularInline):
    model = BehaviorEvent
    extra = 0
    readonly_fields = ['target_name', 'occurred_at', 'frequency_count', 'duration_seconds', 'severity']
    can_delete = False


class ABCEventInline(TabularInline):
    model = ABCEvent
    extra = 0
    readonly_fields = ['occurred_at', 'antecedent', 'behavior_description', 'consequence']
    can_delete = False


@admin.register(SessionRun)
class SessionRunAdmin(ModelAdmin):
    list_display = ['id', 'external_client_id', 'staff_id', 'status', 'started_at', 'submitted_at']
    list_filter = ['status']
    search_fields = ['external_client_id']
    readonly_fields = ['program_snapshot', 'started_at', 'submitted_at', 'reviewed_at', 'created_at', 'updated_at']
    inlines = [TrialEventInline, BehaviorEventInline, ABCEventInline]
    date_hierarchy = 'started_at'
