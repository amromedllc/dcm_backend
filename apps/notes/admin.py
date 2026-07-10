from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from shared.admin import OrganizationScopedAdminMixin
from .models import NoteTemplate, LessonNote, NoteSignature, NoteAssignment


@admin.register(NoteTemplate)
class NoteTemplateAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'is_org_default', 'is_active', 'created_at']
    list_filter = ['is_org_default', 'is_active']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']


class NoteSignatureInline(OrganizationScopedAdminMixin, TabularInline):
    model = NoteSignature
    extra = 0
    readonly_fields = ['signer_id', 'signer_name', 'signer_role', 'signature_type', 'signed_at', 'ip_address_hash']
    can_delete = False


@admin.register(LessonNote)
class LessonNoteAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['id', 'external_client_id', 'staff_id', 'note_date', 'status', 'submitted_at', 'requires_caregiver_signature']
    list_filter = ['status', 'requires_caregiver_signature']
    search_fields = ['external_client_id']
    readonly_fields = ['submitted_at', 'approved_by_id', 'approved_at', 'rejected_by_id', 'rejected_at', 'created_at', 'updated_at']
    date_hierarchy = 'note_date'
    inlines = [NoteSignatureInline]
    fieldsets = (
        ('Identity', {'fields': ('external_client_id', 'session_run', 'staff_id', 'template', 'note_date')}),
        ('Content', {'fields': ('body',)}),
        ('Workflow', {'fields': ('status', 'submitted_at', 'approved_by_id', 'approved_at', 'rejected_by_id', 'rejected_at', 'rejection_reason')}),
        ('Signatures', {'fields': ('requires_caregiver_signature',)}),
        ('Audit', {'fields': ('created_by', 'created_at', 'updated_at')}),
    )


@admin.register(NoteAssignment)
class NoteAssignmentAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['id', 'external_appointment_id', 'template', 'is_filled', 'assigned_by', 'created_at']
    list_filter = ['template']
    readonly_fields = ['note', 'created_at']

