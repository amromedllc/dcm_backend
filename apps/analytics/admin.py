from django.contrib import admin
from unfold.admin import ModelAdmin
from shared.admin import OrganizationScopedAdminMixin
from .models import GraphAnnotation, ClientAnnotation, SavedInsightGraph


@admin.register(GraphAnnotation)
class GraphAnnotationAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['label', 'annotation_type', 'program', 'target', 'date', 'end_date', 'color']
    list_filter = ['annotation_type']
    search_fields = ['label', 'notes']
    date_hierarchy = 'date'
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ClientAnnotation)
class ClientAnnotationAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['label', 'external_client_id', 'date', 'color']
    search_fields = ['label', 'notes']
    date_hierarchy = 'date'
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SavedInsightGraph)
class SavedInsightGraphAdmin(OrganizationScopedAdminMixin, ModelAdmin):
    list_display = ['name', 'external_client_id', 'program', 'visibility', 'display_order']
    list_filter = ['visibility']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']
