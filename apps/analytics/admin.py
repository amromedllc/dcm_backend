from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import GraphAnnotation


@admin.register(GraphAnnotation)
class GraphAnnotationAdmin(ModelAdmin):
    list_display = ['label', 'annotation_type', 'program', 'target', 'date', 'end_date', 'color']
    list_filter = ['annotation_type']
    search_fields = ['label', 'notes']
    date_hierarchy = 'date'
    readonly_fields = ['created_at', 'updated_at']
