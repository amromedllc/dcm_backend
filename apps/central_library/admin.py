import csv
import io
import re
import zipfile
from xml.etree import ElementTree

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django import forms
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.db import transaction
from unfold.admin import ModelAdmin, TabularInline
from .models import (
    CentralProgram, CentralTarget, CentralProgramFolder,
    KnowledgeBaseModule, KnowledgeBaseTopic, KnowledgeBaseImport,
)
from django.utils.safestring import mark_safe
from .imports import (
    split_list as _split_list, bool_value as _bool_value, int_value as _int_value,
    MappingError, MAX_TOPICS, DEFAULT_TOPICS,
    apply_knowledge_base_import, build_module_payload,
    target_option_groups, mapping_from_assignments, assignments_from_mapping,
    mapping_topic_count,
)
from shared.docx_blocks import parse_docx_blocks


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
    fields = ['name', 'measurement_type', 'measurement', 'timer_type', 'sd_text', 'display_order', 'is_visible_to_staff']
    ordering = ['display_order']


@admin.register(CentralProgram)
class CentralProgramAdmin(_SuperuserOnlyAdminMixin, ModelAdmin):
    change_list_template = 'admin/central_library/centralprogram/change_list.html'
    list_display = ['name', 'folder', 'category', 'phase', 'status', 'treatment_area', 'display_order', 'updated_at']
    list_filter = ['category', 'status', 'folder']
    search_fields = ['name', 'treatment_area']
    inlines = [CentralTargetInline]
    readonly_fields = ['created_at', 'updated_at', 'created_by']
    actions = ['move_to_manual_programs_folder']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description='Move selected programs to Manual Programs folder')
    def move_to_manual_programs_folder(self, request, queryset):
        folder, _ = CentralProgramFolder.objects.get_or_create(
            name='Manual Programs',
            defaults={'created_by': request.user},
        )
        updated = queryset.update(folder=folder)
        self.message_user(request, f'Moved {updated} program(s) to Manual Programs.')

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import-csv/', self.admin_site.admin_view(self.import_csv_view), name='central_library_centralprogram_import_csv'),
            path('sample-csv/', self.admin_site.admin_view(self.sample_csv_view), name='central_library_centralprogram_sample_csv'),
        ]
        return custom_urls + urls

    def import_csv_view(self, request):
        if request.method == 'POST':
            form = CentralProgramCsvImportForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    rows = _central_program_rows(form.cleaned_data['file'])
                    created_programs, created_targets, skipped_programs = _import_central_program_rows(
                        rows,
                        folder_name=form.cleaned_data['folder_name'],
                        replace=form.cleaned_data['replace'],
                        user=request.user,
                    )
                except Exception as exc:
                    messages.error(request, f'Import failed: {exc}')
                else:
                    messages.success(
                        request,
                        f'Imported {created_programs} program(s), {created_targets} target(s); '
                        f'skipped {skipped_programs} existing program(s).',
                    )
                    return redirect('admin:central_library_centralprogram_changelist')
        else:
            form = CentralProgramCsvImportForm()

        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'form': form,
            'sample_url': reverse('admin:central_library_centralprogram_sample_csv'),
            'title': 'Import central programs from CSV',
        }
        return TemplateResponse(request, 'admin/central_library/centralprogram/import_csv.html', context)

    def sample_csv_view(self, request):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="central_program_import_sample.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'program_name', 'program_category', 'program_phase', 'treatment_area', 'tags',
            'program_objective', 'program_instructions', 'target_name', 'target_measurement_type',
            'target_measurement', 'sd_text', 'teaching_instructions', 'prompt_levels', 'sub_items',
        ])
        writer.writerow([
            'Colors', 'skill_acquisition', 'teaching', 'Communication', 'colors',
            '', 'Will touch the color in an array of 4 colors', 'Red', 'discrete_trial',
            'percent_correct', 'Touch the red', '', 'Prompt Hierarchy', '',
        ])
        return response

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['import_csv_url'] = reverse('admin:central_library_centralprogram_import_csv')
        extra_context['sample_csv_url'] = reverse('admin:central_library_centralprogram_sample_csv')
        return super().changelist_view(request, extra_context=extra_context)


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
    list_display = ['name', 'program', 'measurement_type', 'measurement', 'timer_type', 'display_order', 'is_visible_to_staff']
    list_filter = ['measurement_type', 'measurement']
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


class CentralProgramCsvImportForm(forms.Form):
    file = forms.FileField(
        label='CSV file',
        help_text='Upload a CSV with the columns from docs/program-target-import-template.csv.',
    )
    folder_name = forms.CharField(
        initial='Manual Programs',
        help_text='Central Library folder to create/use for the imported programs.',
    )
    replace = forms.BooleanField(
        required=False,
        help_text='Replace programs with the same name in this folder.',
    )


DOCX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MAX_DOCX_BYTES = 10 * 1024 * 1024


class KnowledgeBaseDocxForm(forms.Form):
    file = forms.FileField(
        label='Word document',
        help_text='A .docx article. The next screen lets you map its content onto module and topic fields.',
    )

    def clean_file(self):
        f = self.cleaned_data['file']
        if not (f.name or '').lower().endswith('.docx'):
            raise forms.ValidationError('Upload a Word .docx file.')
        if f.content_type and f.content_type != DOCX_CONTENT_TYPE:
            raise forms.ValidationError('That file is not a valid Word .docx document.')
        if f.size > MAX_DOCX_BYTES:
            raise forms.ValidationError(f'Document must be under {MAX_DOCX_BYTES // (1024 * 1024)}MB.')
        return f


def _block_badge(block: dict) -> str:
    kind = block.get('kind')
    if kind == 'heading':
        return f"Heading {block.get('level') or 1}"
    if kind == 'list_item':
        return 'Numbered item' if block.get('list') == 'number' else 'Bullet'
    if kind == 'table':
        return 'Table'
    return 'Paragraph'


def _csv_rows(uploaded_file):
    raw = uploaded_file.read()
    for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('Could not decode CSV. Save the file as UTF-8 CSV and try again.')
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


_PROGRAM_CATEGORY_MAP = {
    'skill_acquisition': CentralProgram.Category.SKILL_ACQUISITION,
    'skill acquisition': CentralProgram.Category.SKILL_ACQUISITION,
    'behavior_reduction': CentralProgram.Category.BEHAVIOR_REDUCTION,
    'behavior reduction': CentralProgram.Category.BEHAVIOR_REDUCTION,
    'abc_recording': CentralProgram.Category.ABC_RECORDING,
    'abc recording': CentralProgram.Category.ABC_RECORDING,
    'telehealth': CentralProgram.Category.TELEHEALTH,
}

_PROGRAM_PHASE_MAP = {
    'baseline': CentralProgram.Phase.BASELINE,
    'teaching': CentralProgram.Phase.TEACHING,
    'active': CentralProgram.Phase.TEACHING,
    'generalizing': CentralProgram.Phase.GENERALIZING,
    'generalization': CentralProgram.Phase.GENERALIZING,
    'maintenance': CentralProgram.Phase.MAINTENANCE,
    'mastered': CentralProgram.Phase.MASTERED,
    'on_hold': CentralProgram.Phase.ON_HOLD,
    'on hold': CentralProgram.Phase.ON_HOLD,
    'hold': CentralProgram.Phase.ON_HOLD,
}

_TARGET_MEASUREMENT_TYPE_MAP = {
    'discrete_trial': CentralTarget.MeasurementType.DISCRETE_TRIAL,
    'discrete trial': CentralTarget.MeasurementType.DISCRETE_TRIAL,
    'duration': CentralTarget.MeasurementType.DURATION,
    'rate': CentralTarget.MeasurementType.RATE,
    'task_analysis': CentralTarget.MeasurementType.TASK_ANALYSIS,
    'task analysis': CentralTarget.MeasurementType.TASK_ANALYSIS,
    'set_of_targets': CentralTarget.MeasurementType.SET_OF_TARGETS,
    'set of targets': CentralTarget.MeasurementType.SET_OF_TARGETS,
    'shaping': CentralTarget.MeasurementType.SHAPING,
    'instructions': CentralTarget.MeasurementType.INSTRUCTIONS,
}


def _clean(value):
    return str(value or '').strip()


def _choice(value, mapping, default):
    return mapping.get(_clean(value).lower(), default)


def _pipe_or_comma_list(value):
    raw = _clean(value)
    if not raw:
        return []
    delimiter = '|' if '|' in raw else ','
    return [part.strip() for part in raw.split(delimiter) if part.strip()]


def _prompt_levels(value):
    labels = _pipe_or_comma_list(value)
    if not labels or labels == ['Prompt Hierarchy']:
        return []
    return [
        {
            'label': label,
            'score': index,
            'abbreviation': label[:3].upper(),
            'is_success': index == len(labels) - 1,
        }
        for index, label in enumerate(labels)
    ]


def _central_program_rows(uploaded_file):
    name = uploaded_file.name.lower()
    if not name.endswith('.csv'):
        raise ValueError('Only .csv uploads are supported for Central Program import.')
    return _csv_rows(uploaded_file)


def _group_central_program_rows(rows):
    programs = {}
    for row_number, row in enumerate(rows, start=2):
        program_name = _clean(row.get('program_name'))
        target_name = _clean(row.get('target_name'))
        if not program_name and not target_name:
            continue
        if not program_name or not target_name:
            raise ValueError(f'Row {row_number}: program_name and target_name are required.')
        program = programs.setdefault(program_name, {
            'name': program_name,
            'category': _choice(row.get('program_category'), _PROGRAM_CATEGORY_MAP, CentralProgram.Category.SKILL_ACQUISITION),
            'phase': _choice(row.get('program_phase'), _PROGRAM_PHASE_MAP, CentralProgram.Phase.TEACHING),
            'treatment_area': _clean(row.get('treatment_area')),
            'tags': _pipe_or_comma_list(row.get('tags')),
            'objective': _clean(row.get('program_objective')),
            'instructions': _clean(row.get('program_instructions')),
            'targets': [],
        })
        if not program['instructions'] and _clean(row.get('program_instructions')):
            program['instructions'] = _clean(row.get('program_instructions'))
        program['targets'].append({
            'name': target_name,
            'measurement_type': _choice(
                row.get('target_measurement_type'),
                _TARGET_MEASUREMENT_TYPE_MAP,
                CentralTarget.MeasurementType.DISCRETE_TRIAL,
            ),
            'measurement': _clean(row.get('target_measurement')),
            'sub_items': [
                {'key': item.lower().replace(' ', '_'), 'label': item}
                for item in _pipe_or_comma_list(row.get('sub_items'))
            ],
            'sd_text': _clean(row.get('sd_text')),
            'teaching_instructions': _clean(row.get('teaching_instructions')),
            'prompting_levels': _prompt_levels(row.get('prompt_levels')),
        })
    return list(programs.values())


def _import_central_program_rows(rows, *, folder_name, replace, user):
    programs = _group_central_program_rows(rows)
    folder, _ = CentralProgramFolder.objects.get_or_create(
        name=folder_name,
        defaults={'created_by': user},
    )
    created_programs = 0
    skipped_programs = 0
    created_targets = 0

    with transaction.atomic():
        for index, program_data in enumerate(programs):
            existing = CentralProgram.objects.filter(folder=folder, name=program_data['name']).first()
            if existing and not replace:
                skipped_programs += 1
                continue
            if existing:
                existing.delete()
            program = CentralProgram.objects.create(
                name=program_data['name'],
                category=program_data['category'],
                phase=program_data['phase'],
                status=CentralProgram.Status.ACTIVE,
                treatment_area=program_data['treatment_area'],
                tags=program_data['tags'],
                objective=program_data['objective'],
                instructions=program_data['instructions'],
                folder=folder,
                display_order=index * 10,
                created_by=user,
            )
            created_programs += 1
            for target_index, target_data in enumerate(program_data['targets']):
                CentralTarget.objects.create(
                    program=program,
                    display_order=target_index * 10,
                    **target_data,
                )
                created_targets += 1
    return created_programs, created_targets, skipped_programs


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
            path('import-docx/', self.admin_site.admin_view(self.import_docx_view), name='central_library_knowledgebasemodule_import_docx'),
            path('import-docx/<int:import_id>/', self.admin_site.admin_view(self.map_docx_view), name='central_library_knowledgebasemodule_map_docx'),
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
        extra_context['import_docx_url'] = reverse('admin:central_library_knowledgebasemodule_import_docx')
        extra_context['sample_url'] = reverse('admin:central_library_knowledgebasemodule_sample_csv')
        return super().changelist_view(request, extra_context=extra_context)

    # -- Word (.docx) import: upload, then map blocks onto fields -------------

    def import_docx_view(self, request):
        """Step 1 — upload a .docx, parse it into blocks, create a draft
        KnowledgeBaseImport, and go to the mapping screen."""
        if not request.user.is_superuser:
            raise PermissionDenied
        if request.method == 'POST':
            form = KnowledgeBaseDocxForm(request.POST, request.FILES)
            if form.is_valid():
                uploaded = form.cleaned_data['file']
                try:
                    parsed = parse_docx_blocks(uploaded)
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    if not parsed['blocks']:
                        messages.error(request, 'No readable text was found in that document.')
                    else:
                        uploaded.seek(0)
                        kb_import = KnowledgeBaseImport.objects.create(
                            file=uploaded,
                            original_filename=uploaded.name or 'document.docx',
                            blocks=parsed['blocks'],
                            image_count=parsed['image_count'],
                            mapping={},
                            created_by=request.user,
                        )
                        return redirect(
                            'admin:central_library_knowledgebasemodule_map_docx',
                            import_id=kb_import.id,
                        )
        else:
            form = KnowledgeBaseDocxForm()

        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'form': form,
            'title': 'Import a Word document',
            'recent_imports': (
                KnowledgeBaseImport.objects
                .exclude(status=KnowledgeBaseImport.Status.DISCARDED)
                .order_by('-updated_at')[:10]
            ),
        }
        return TemplateResponse(request, 'admin/central_library/knowledgebasemodule/import_docx.html', context)

    def map_docx_view(self, request, import_id):
        """Step 2 — assign each parsed block to a module/topic field, preview
        the result, then apply it as a KnowledgeBaseModule (+ topics)."""
        if not request.user.is_superuser:
            raise PermissionDenied
        try:
            kb_import = KnowledgeBaseImport.objects.get(pk=import_id)
        except KnowledgeBaseImport.DoesNotExist:
            messages.error(request, 'That import no longer exists.')
            return redirect('admin:central_library_knowledgebasemodule_import_docx')

        blocks = kb_import.blocks or []
        icon_choices = KnowledgeBaseModule.Icon.choices
        already_applied = kb_import.status == KnowledgeBaseImport.Status.APPLIED

        # Settings echoed back into the form across submits.
        settings = {
            'slug': (kb_import.target_module.slug if kb_import.target_module_id else ''),
            'icon': KnowledgeBaseModule.Icon.BOOK,
            'display_order': 0,
            'is_active': True,
            'replace_topics': True,
            'topic_count': mapping_topic_count(kb_import.mapping),
        }
        preview = None
        preview_error = None

        if request.method == 'POST' and not already_applied:
            action = request.POST.get('action', 'preview')
            settings['slug'] = request.POST.get('slug', settings['slug']).strip()
            settings['icon'] = request.POST.get('icon', settings['icon'])
            settings['display_order'] = _int_value(request.POST.get('display_order'), 0)
            settings['is_active'] = request.POST.get('is_active') == 'on'
            settings['replace_topics'] = request.POST.get('replace_topics') == 'on'
            settings['topic_count'] = max(1, min(_int_value(request.POST.get('topic_count'), DEFAULT_TOPICS), MAX_TOPICS))

            assignments = {
                block['id']: request.POST.get(f'assign_{block["id"]}', '')
                for block in blocks
            }
            mapping = mapping_from_assignments(assignments, blocks, settings['topic_count'])
            kb_import.mapping = mapping
            kb_import.save(update_fields=['mapping', 'updated_at'])

            if action == 'rebuild':
                messages.info(request, f"Topic slots set to {settings['topic_count']}.")
            elif action in ('preview', 'apply'):
                try:
                    slug, module_defaults, topics = build_module_payload(
                        mapping, blocks,
                        slug=settings['slug'], icon=settings['icon'],
                        display_order=settings['display_order'], is_active=settings['is_active'],
                    )
                except MappingError as exc:
                    preview_error = str(exc)
                else:
                    if action == 'apply':
                        module = apply_knowledge_base_import(
                            kb_import, user=request.user,
                            slug=settings['slug'], icon=settings['icon'],
                            display_order=settings['display_order'], is_active=settings['is_active'],
                            replace_topics=settings['replace_topics'],
                        )
                        messages.success(
                            request,
                            f'Applied "{module.title}" ({module.slug}) with {len(topics)} topic(s).',
                        )
                        return redirect('admin:central_library_knowledgebasemodule_changelist')
                    exists = KnowledgeBaseModule.objects.filter(slug=slug).exists()
                    preview = {
                        'slug': slug,
                        'module': module_defaults,
                        'topics': topics,
                        'will_update': exists,
                    }

        assignments_map = assignments_from_mapping(kb_import.mapping)
        option_groups = [
            {'label': label, 'options': options}
            for label, options in target_option_groups(settings['topic_count'])
        ]
        block_rows = [
            {
                'block': block,
                'html': mark_safe(block.get('html') or block.get('text', '')),
                'selected': assignments_map.get(block['id'], ''),
                'badge': _block_badge(block),
            }
            for block in blocks
        ]

        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': f'Map "{kb_import.original_filename}"',
            'kb_import': kb_import,
            'already_applied': already_applied,
            'block_rows': block_rows,
            'option_groups': option_groups,
            'icon_choices': icon_choices,
            'settings': settings,
            'topic_slot_options': list(range(1, MAX_TOPICS + 1)),
            'max_topics': MAX_TOPICS,
            'preview': preview,
            'preview_error': preview_error,
        }
        return TemplateResponse(request, 'admin/central_library/knowledgebasemodule/map_docx.html', context)


@admin.register(KnowledgeBaseTopic)
class KnowledgeBaseTopicAdmin(_SuperuserOnlyAdminMixin, ModelAdmin):
    list_display = ['title', 'module', 'display_order', 'is_active', 'updated_at']
    list_filter = ['is_active', 'module']
    search_fields = ['title', 'summary', 'items']


@admin.register(KnowledgeBaseImport)
class KnowledgeBaseImportAdmin(_SuperuserOnlyAdminMixin, ModelAdmin):
    """Audit view for Word-doc imports. Authoring happens in the web app
    (/knowledge-base/import); this is read-only history."""
    list_display = ['original_filename', 'status', 'target_module', 'created_by', 'updated_at']
    list_filter = ['status']
    search_fields = ['original_filename']
    readonly_fields = [
        'file', 'original_filename', 'status', 'image_count', 'blocks', 'mapping',
        'target_module', 'created_by', 'created_at', 'updated_at',
    ]

    def has_add_permission(self, request, obj=None):
        return False
