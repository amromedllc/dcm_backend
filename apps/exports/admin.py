from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import Export


@admin.register(Export)
class ExportAdmin(ModelAdmin):
    list_display = ['id', 'export_type', 'status', 'created_by_id', 'row_count',
                    'file_size_bytes', 'download_count', 'generated_at', 'created_at']
    list_filter = ['export_type', 'status']
    readonly_fields = ['file_path', 'file_size_bytes', 'row_count', 'error_message',
                       'generated_at', 'download_count', 'last_downloaded_at', 'created_at', 'updated_at']
    date_hierarchy = 'created_at'
