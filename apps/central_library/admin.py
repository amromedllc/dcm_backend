import csv
import io
import json
import re
import zipfile
from xml.etree import ElementTree

from django.contrib import admin
from django.contrib import messages
from django import forms
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from unfold.admin import ModelAdmin, TabularInline
from .models import (
    CentralProgram, CentralTarget, CentralProgramFolder,
    KnowledgeBaseModule, KnowledgeBaseTopic,
)


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
    list_display = ['name', 'folder', 'category', 'phase', 'status', 'treatment_area', 'display_order', 'updated_at']
    list_filter = ['category', 'status', 'folder']
    search_fields = ['name', 'treatment_area']
    inlines = [CentralTargetInline]
    readonly_fields = ['created_at', 'updated_at', 'created_by']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(CentralProgramFolder)
class CentralProgramFolderAdmin(_SuperuserOnlyAdminMixin, ModelAdmin):
    list_display = ['name', 'display_order', 'updated_at']
    search_fields = ['name']
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


class KnowledgeBaseTopicInline(_SuperuserOnlyAdminMixin, TabularInline):
    model = KnowledgeBaseTopic
    extra = 0
    fields = ['title', 'summary', 'items', 'display_order', 'is_active']
    ordering = ['display_order', 'title']


class KnowledgeBaseImportForm(forms.Form):
    file = forms.FileField(
        help_text=(
            'Upload .csv or .xlsx with columns: module_slug, module_title, path, icon, '
            'overview, audience, module_order, module_active, topic_title, '
            'topic_summary, topic_items, topic_order, topic_active.'
        )
    )


def _split_list(value):
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    if text.startswith('['):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    separator = '|' if '|' in text else '\n' if '\n' in text else ','
    return [item.strip() for item in text.split(separator) if item.strip()]


def _bool_value(value, default=True):
    if value in (None, ''):
        return default
    return str(value).strip().lower() not in {'0', 'false', 'no', 'n', 'inactive'}


def _int_value(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _csv_rows(uploaded_file):
    text = uploaded_file.read().decode('utf-8-sig')
    return list(csv.DictReader(io.StringIO(text)))


def _xlsx_rows(uploaded_file):
    def column_index(cell_ref):
        letters = ''.join(re.findall(r'[A-Z]+', cell_ref or ''))
        total = 0
        for letter in letters:
            total = total * 26 + (ord(letter) - ord('A') + 1)
        return max(total - 1, 0)

    with zipfile.ZipFile(uploaded_file) as archive:
        shared_strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared_root = ElementTree.fromstring(archive.read('xl/sharedStrings.xml'))
            ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            for si in shared_root.findall('x:si', ns):
                shared_strings.append(''.join(node.text or '' for node in si.findall('.//x:t', ns)))

        sheet_name = 'xl/worksheets/sheet1.xml'
        root = ElementTree.fromstring(archive.read(sheet_name))
        ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        rows = []
        for row in root.findall('.//x:sheetData/x:row', ns):
            values = []
            for cell in row.findall('x:c', ns):
                idx = column_index(cell.attrib.get('r'))
                while len(values) < idx:
                    values.append('')
                value = cell.find('x:v', ns)
                text = value.text if value is not None else ''
                if cell.attrib.get('t') == 's' and text:
                    text = shared_strings[int(text)]
                values.append(text or '')
            rows.append(values)
    if not rows:
        return []
    headers = [str(header).strip() for header in rows[0]]
    return [dict(zip(headers, row)) for row in rows[1:] if any(str(cell).strip() for cell in row)]


def _knowledge_rows(uploaded_file):
    name = uploaded_file.name.lower()
    if name.endswith('.csv'):
        return _csv_rows(uploaded_file)
    if name.endswith('.xlsx'):
        return _xlsx_rows(uploaded_file)
    raise ValueError('Only .csv and .xlsx uploads are supported.')


def _import_knowledge_base_rows(rows, user):
    imported_modules: set[str] = set()
    imported_topics = 0
    valid_icons = {choice[0] for choice in KnowledgeBaseModule.Icon.choices}

    for row_number, row in enumerate(rows, start=2):
        slug = str(row.get('module_slug') or '').strip()
        title = str(row.get('module_title') or '').strip()
        topic_title = str(row.get('topic_title') or '').strip()
        if not slug or not title or not topic_title:
            raise ValueError(f'Row {row_number}: module_slug, module_title, and topic_title are required.')

        icon = str(row.get('icon') or KnowledgeBaseModule.Icon.BOOK).strip()
        if icon not in valid_icons:
            icon = KnowledgeBaseModule.Icon.BOOK

        module, created = KnowledgeBaseModule.objects.update_or_create(
            slug=slug,
            defaults={
                'title': title,
                'path': str(row.get('path') or '').strip(),
                'icon': icon,
                'overview': str(row.get('overview') or '').strip(),
                'audience': _split_list(row.get('audience')),
                'display_order': _int_value(row.get('module_order')),
                'is_active': _bool_value(row.get('module_active')),
            },
        )
        if created:
            module.created_by = user
            module.save(update_fields=['created_by'])
        imported_modules.add(module.slug)

        KnowledgeBaseTopic.objects.update_or_create(
            module=module,
            title=topic_title,
            defaults={
                'summary': str(row.get('topic_summary') or '').strip(),
                'items': _split_list(row.get('topic_items')),
                'display_order': _int_value(row.get('topic_order')),
                'is_active': _bool_value(row.get('topic_active')),
            },
        )
        imported_topics += 1

    return len(imported_modules), imported_topics


@admin.register(KnowledgeBaseModule)
class KnowledgeBaseModuleAdmin(_SuperuserOnlyAdminMixin, ModelAdmin):
    change_list_template = 'admin/central_library/knowledgebasemodule/change_list.html'
    list_display = ['title', 'slug', 'path', 'icon', 'display_order', 'is_active', 'updated_at']
    list_filter = ['is_active', 'icon']
    search_fields = ['title', 'slug', 'overview', 'path']
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ['created_at', 'updated_at', 'created_by']
    inlines = [KnowledgeBaseTopicInline]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import/', self.admin_site.admin_view(self.import_view), name='central_library_knowledgebasemodule_import'),
            path('sample-csv/', self.admin_site.admin_view(self.sample_csv_view), name='central_library_knowledgebasemodule_sample_csv'),
        ]
        return custom_urls + urls

    def import_view(self, request):
        if request.method == 'POST':
            form = KnowledgeBaseImportForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    rows = _knowledge_rows(form.cleaned_data['file'])
                    module_count, topic_count = _import_knowledge_base_rows(rows, request.user)
                except Exception as exc:
                    messages.error(request, f'Import failed: {exc}')
                else:
                    messages.success(request, f'Imported {module_count} module(s) and {topic_count} topic row(s).')
                    return redirect('admin:central_library_knowledgebasemodule_changelist')
        else:
            form = KnowledgeBaseImportForm()

        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'form': form,
            'sample_url': reverse('admin:central_library_knowledgebasemodule_sample_csv'),
            'title': 'Import knowledge base',
        }
        return TemplateResponse(request, 'admin/central_library/knowledgebasemodule/import.html', context)

    def sample_csv_view(self, request):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="knowledge_base_import_sample.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'module_slug', 'module_title', 'path', 'icon', 'overview', 'audience',
            'module_order', 'module_active', 'topic_title', 'topic_summary',
            'topic_items', 'topic_order', 'topic_active',
        ])
        writer.writerow([
            'dashboard', 'Dashboard', '/dashboard', 'bar_chart',
            'Short overview shown at the top of the module.',
            'Admin|Supervisor|Staff',
            '10', 'true', 'What users see', 'Short topic summary.',
            'First bullet|Second bullet|Third bullet', '10', 'true',
        ])
        return response

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['import_url'] = reverse('admin:central_library_knowledgebasemodule_import')
        extra_context['sample_url'] = reverse('admin:central_library_knowledgebasemodule_sample_csv')
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(KnowledgeBaseTopic)
class KnowledgeBaseTopicAdmin(_SuperuserOnlyAdminMixin, ModelAdmin):
    list_display = ['title', 'module', 'display_order', 'is_active', 'updated_at']
    list_filter = ['is_active', 'module']
    search_fields = ['title', 'summary', 'items']
