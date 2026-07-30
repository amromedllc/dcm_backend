"""
Synchronous file generation for exports — runs inline in the request that
requested it (no Celery/Redis dependency).

Each function:
1. Marks the Export row as PROCESSING
2. Generates the file in memory
3. Writes to Django's default_storage (local media in dev, S3 in production)
4. Marks the row COMPLETED with file_path, file_size_bytes, row_count
5. On any exception: marks the row FAILED with error_message and returns
   normally (does not raise) — the caller always gets back a row with a
   final status, whether that's completed or failed.
"""
import csv
import io
import zipfile
from datetime import date, timedelta
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from shared.audit import log_export

_DEFAULT_GRAPH_DAYS = 90  # matches apps.analytics.api._DEFAULT_DAYS


def _mark_processing(export):
    export.status = 'processing'
    export.save(update_fields=['status'])


def _mark_completed(export, file_path: str, file_size: int, row_count: int):
    export.status = 'completed'
    export.file_path = file_path
    export.file_size_bytes = file_size
    export.row_count = row_count
    export.generated_at = timezone.now()
    export.save(update_fields=['status', 'file_path', 'file_size_bytes', 'row_count', 'generated_at'])
    log_export(export.created_by_id, export.id, export.export_type)


def _mark_failed(export, error: str):
    export.status = 'failed'
    export.error_message = error
    export.save(update_fields=['status', 'error_message'])


def _save_file(content: bytes, filename: str) -> tuple[str, int]:
    """Writes bytes to storage and returns (storage_key, size_bytes)."""
    storage_path = f'exports/{filename}'
    # Overwrite if the file already exists (re-generation case)
    if default_storage.exists(storage_path):
        default_storage.delete(storage_path)
    saved_path = default_storage.save(storage_path, ContentFile(content))
    return saved_path, len(content)


# ---------------------------------------------------------------------------
# Trial CSV
# ---------------------------------------------------------------------------

def generate_trial_csv(export_id: int) -> None:
    from .models import Export
    from apps.sessions.models import TrialEvent

    export = Export.objects.get(id=export_id)
    try:
        _mark_processing(export)

        params = export.params
        qs = TrialEvent.objects.filter(
            target_id__in=_target_ids_for_program(params.get('program_id')),
        )
        if params.get('date_from'):
            qs = qs.filter(recorded_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(recorded_at__date__lte=params['date_to'])
        qs = qs.select_related('session_run').order_by('recorded_at')

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'session_id', 'session_date', 'target_id', 'target_name',
            'trial_number', 'response_score', 'prompt_level', 'recorded_at', 'notes',
        ])
        row_count = 0
        for event in qs.iterator(chunk_size=500):
            writer.writerow([
                event.session_run_id,
                event.session_run.started_at.date(),
                event.target_id,
                event.target_name,
                event.trial_number,
                event.response_score,
                event.prompt_level_label,
                event.recorded_at.isoformat(),
                event.staff_notes,
            ])
            row_count += 1

        content = buf.getvalue().encode('utf-8')
        filename = f'trial_csv_{export_id}_{timezone.now():%Y%m%d_%H%M%S}.csv'
        path, size = _save_file(content, filename)
        _mark_completed(export, path, size, row_count)

    except Exception as exc:
        _mark_failed(export, str(exc))


# ---------------------------------------------------------------------------
# Behavior CSV
# ---------------------------------------------------------------------------

def generate_behavior_csv(export_id: int) -> None:
    from .models import Export
    from apps.sessions.models import BehaviorEvent

    export = Export.objects.get(id=export_id)
    try:
        _mark_processing(export)
        params = export.params

        qs = BehaviorEvent.objects.filter(
            target_id__in=_target_ids_for_program(params.get('program_id')),
        )
        if params.get('date_from'):
            qs = qs.filter(occurred_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(occurred_at__date__lte=params['date_to'])
        qs = qs.order_by('occurred_at')

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'session_id', 'target_id', 'target_name', 'occurred_at',
            'frequency_count', 'duration_seconds', 'severity', 'notes',
        ])
        row_count = 0
        for event in qs.iterator(chunk_size=500):
            writer.writerow([
                event.session_run_id,
                event.target_id,
                event.target_name,
                event.occurred_at.isoformat(),
                event.frequency_count,
                event.duration_seconds or '',
                event.severity,
                event.notes,
            ])
            row_count += 1

        content = buf.getvalue().encode('utf-8')
        filename = f'behavior_csv_{export_id}_{timezone.now():%Y%m%d_%H%M%S}.csv'
        path, size = _save_file(content, filename)
        _mark_completed(export, path, size, row_count)

    except Exception as exc:
        _mark_failed(export, str(exc))


# ---------------------------------------------------------------------------
# ABC CSV
# ---------------------------------------------------------------------------

def generate_abc_csv(export_id: int) -> None:
    from .models import Export
    from apps.sessions.models import ABCEvent, SessionRun

    export = Export.objects.get(id=export_id)
    try:
        _mark_processing(export)
        params = export.params

        session_qs = SessionRun.objects.filter(client_id=params['client_id'])
        qs = ABCEvent.objects.filter(session_run__in=session_qs)
        if params.get('date_from'):
            qs = qs.filter(occurred_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(occurred_at__date__lte=params['date_to'])
        qs = qs.order_by('occurred_at')

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'session_id', 'occurred_at', 'antecedent',
            'behavior', 'consequence', 'setting', 'staff_response', 'notes',
        ])
        row_count = 0
        for event in qs.iterator(chunk_size=500):
            writer.writerow([
                event.session_run_id,
                event.occurred_at.isoformat(),
                event.antecedent,
                event.behavior_description,
                event.consequence,
                event.setting,
                event.staff_response,
                event.notes,
            ])
            row_count += 1

        content = buf.getvalue().encode('utf-8')
        filename = f'abc_csv_{export_id}_{timezone.now():%Y%m%d_%H%M%S}.csv'
        path, size = _save_file(content, filename)
        _mark_completed(export, path, size, row_count)

    except Exception as exc:
        _mark_failed(export, str(exc))


# ---------------------------------------------------------------------------
# Raw data ZIP — trials + behaviors + ABC in one archive
# ---------------------------------------------------------------------------

def generate_raw_zip(export_id: int) -> None:
    from .models import Export
    from apps.sessions.models import TrialEvent, BehaviorEvent, ABCEvent

    export = Export.objects.get(id=export_id)
    try:
        _mark_processing(export)
        params = export.params
        program_id = params.get('program_id')
        client_id = params.get('client_id')

        zip_buf = io.BytesIO()
        total_rows = 0

        with zipfile.ZipFile(zip_buf, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            # Trials
            target_ids = _target_ids_for_program(program_id)
            trial_qs = TrialEvent.objects.filter(target_id__in=target_ids)
            if params.get('date_from'):
                trial_qs = trial_qs.filter(recorded_at__date__gte=params['date_from'])
            if params.get('date_to'):
                trial_qs = trial_qs.filter(recorded_at__date__lte=params['date_to'])
            trial_qs = trial_qs.select_related('session_run').order_by('recorded_at')

            trial_buf = io.StringIO()
            tw = csv.writer(trial_buf)
            tw.writerow(['session_id', 'session_date', 'target_id', 'target_name',
                         'trial_number', 'response_score', 'prompt_level', 'recorded_at', 'notes'])
            for event in trial_qs.iterator(chunk_size=500):
                tw.writerow([event.session_run_id, event.session_run.started_at.date(),
                              event.target_id, event.target_name, event.trial_number,
                              event.response_score, event.prompt_level_label,
                              event.recorded_at.isoformat(), event.staff_notes])
                total_rows += 1
            zf.writestr('trials.csv', trial_buf.getvalue())

            # Behaviors
            behavior_qs = BehaviorEvent.objects.filter(target_id__in=target_ids)
            if params.get('date_from'):
                behavior_qs = behavior_qs.filter(occurred_at__date__gte=params['date_from'])
            if params.get('date_to'):
                behavior_qs = behavior_qs.filter(occurred_at__date__lte=params['date_to'])

            behavior_buf = io.StringIO()
            bw = csv.writer(behavior_buf)
            bw.writerow(['session_id', 'target_id', 'target_name', 'occurred_at',
                         'frequency_count', 'duration_seconds', 'severity', 'notes'])
            for event in behavior_qs.iterator(chunk_size=500):
                bw.writerow([event.session_run_id, event.target_id, event.target_name,
                              event.occurred_at.isoformat(), event.frequency_count,
                              event.duration_seconds or '', event.severity, event.notes])
                total_rows += 1
            zf.writestr('behaviors.csv', behavior_buf.getvalue())

            # ABC
            if client_id:
                abc_qs = ABCEvent.objects.filter(session_run__client_id=client_id)
                if params.get('date_from'):
                    abc_qs = abc_qs.filter(occurred_at__date__gte=params['date_from'])
                if params.get('date_to'):
                    abc_qs = abc_qs.filter(occurred_at__date__lte=params['date_to'])

                abc_buf = io.StringIO()
                aw = csv.writer(abc_buf)
                aw.writerow(['session_id', 'occurred_at', 'antecedent',
                             'behavior', 'consequence', 'setting', 'staff_response', 'notes'])
                for event in abc_qs.iterator(chunk_size=500):
                    aw.writerow([event.session_run_id, event.occurred_at.isoformat(),
                                 event.antecedent, event.behavior_description, event.consequence,
                                 event.setting, event.staff_response, event.notes])
                    total_rows += 1
                zf.writestr('abc.csv', abc_buf.getvalue())

        content = zip_buf.getvalue()
        filename = f'raw_zip_{export_id}_{timezone.now():%Y%m%d_%H%M%S}.zip'
        path, size = _save_file(content, filename)
        _mark_completed(export, path, size, total_rows)

    except Exception as exc:
        _mark_failed(export, str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _target_ids_for_program(program_id: int | None) -> list[int]:
    if not program_id:
        return []
    from apps.programs.models import Target
    return list(Target.objects.filter(program_id=program_id).values_list('id', flat=True))


# ---------------------------------------------------------------------------
# Notes CSV — all notes for a client
# ---------------------------------------------------------------------------

def generate_notes_csv(export_id: int) -> None:
    from .models import Export
    from apps.notes.models import LessonNote

    export = Export.objects.get(id=export_id)
    try:
        _mark_processing(export)
        params = export.params

        qs = LessonNote.objects.filter(
            external_client_id=params['client_id']
        ).select_related('staff', 'template').order_by('note_date')

        if params.get('date_from'):
            qs = qs.filter(note_date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(note_date__lte=params['date_to'])
        if params.get('status'):
            qs = qs.filter(status=params['status'])

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'note_id', 'note_date', 'status', 'staff_email', 'staff_name',
            'template_name', 'submitted_at', 'approved_at', 'rejected_at',
            'rejection_reason', 'session_run_id', 'created_at',
        ])
        row_count = 0
        for note in qs.iterator(chunk_size=500):
            writer.writerow([
                note.id,
                note.note_date,
                note.status,
                note.staff.email if note.staff else '',
                note.staff.full_name if note.staff else '',
                note.template.name if note.template else '',
                note.submitted_at.isoformat() if note.submitted_at else '',
                note.approved_at.isoformat() if note.approved_at else '',
                note.rejected_at.isoformat() if note.rejected_at else '',
                note.rejection_reason,
                note.session_run_id or '',
                note.created_at.isoformat(),
            ])
            row_count += 1

        content = buf.getvalue().encode('utf-8')
        filename = f'notes_csv_{export_id}_{timezone.now():%Y%m%d_%H%M%S}.csv'
        path, size = _save_file(content, filename)
        _mark_completed(export, path, size, row_count)

    except Exception as exc:
        _mark_failed(export, str(exc))


# ---------------------------------------------------------------------------
# Sessions CSV — all sessions for a client
# ---------------------------------------------------------------------------

def generate_sessions_csv(export_id: int) -> None:
    from .models import Export
    from apps.sessions.models import SessionRun
    from django.db.models import Count

    export = Export.objects.get(id=export_id)
    try:
        _mark_processing(export)
        params = export.params

        qs = SessionRun.objects.filter(
            external_client_id=params['client_id']
        ).select_related('staff').annotate(
            trial_count=Count('trial_events'),
            behavior_count=Count('behavior_events'),
        ).order_by('started_at')

        if params.get('date_from'):
            qs = qs.filter(started_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(started_at__date__lte=params['date_to'])
        if params.get('status'):
            qs = qs.filter(status=params['status'])

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'session_id', 'status', 'staff_email', 'staff_name',
            'started_at', 'ended_at', 'submitted_at', 'reviewed_at',
            'trial_count', 'behavior_count', 'rejection_reason',
        ])
        row_count = 0
        for session in qs.iterator(chunk_size=500):
            writer.writerow([
                session.id,
                session.status,
                session.staff.email if session.staff else '',
                session.staff.full_name if session.staff else '',
                session.started_at.isoformat(),
                session.ended_at.isoformat() if session.ended_at else '',
                session.submitted_at.isoformat() if session.submitted_at else '',
                session.reviewed_at.isoformat() if session.reviewed_at else '',
                session.trial_count,
                session.behavior_count,
                session.rejection_reason,
            ])
            row_count += 1

        content = buf.getvalue().encode('utf-8')
        filename = f'sessions_csv_{export_id}_{timezone.now():%Y%m%d_%H%M%S}.csv'
        path, size = _save_file(content, filename)
        _mark_completed(export, path, size, row_count)

    except Exception as exc:
        _mark_failed(export, str(exc))


# ---------------------------------------------------------------------------
# Program graph — PNG / SVG, mirrors apps/web/components/analytics/ProgramGraph.tsx
# ---------------------------------------------------------------------------

# Same categorical palette as apps/web/lib/chart-theme.ts::SERIES_LIGHT, so an
# exported image reads as the same chart a supervisor already sees on screen.
_GRAPH_PALETTE = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444', '#06b6d4', '#ec4899', '#84cc16']
_GRAPH_LINESTYLE = {'dashed': '--', 'dotted': ':', 'solid': '-'}


def _render_program_graph(program, target_ids: list[int], date_from: date, date_to: date, image_format: str) -> bytes:
    import matplotlib
    matplotlib.use('Agg')  # headless — no display server on the app server
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    from apps.analytics.models import GraphAnnotation
    from apps.analytics.services import get_trial_data_by_day
    from apps.programs.models import Target

    if not target_ids:
        target_ids = list(Target.objects.filter(program_id=program.id).values_list('id', flat=True))

    points = get_trial_data_by_day(target_ids, date_from, date_to)
    # Same query the live annotations endpoint uses (list_annotations) — no
    # date filtering there either, so parity means not filtering here.
    annotations = list(GraphAnnotation.objects.filter(program_id=program.id))

    by_target: dict[int, dict] = {}
    all_dates: set = set()
    for p in points:
        entry = by_target.setdefault(p['target_id'], {'name': p['target_name'], 'series': {}})
        entry['series'][p['date']] = p['pct_correct']
        all_dates.add(p['date'])
    sorted_dates = sorted(all_dates)

    fig, ax = plt.subplots(figsize=(11, 5), dpi=150)

    if not sorted_dates:
        ax.text(0.5, 0.5, 'No trial data for the selected period.', ha='center', va='center',
                 transform=ax.transAxes, fontsize=11, color='#94a3b8')
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
    else:
        # Phase ranges drawn first so lines render on top of the shading.
        for a in annotations:
            if a.annotation_type == 'phase_range' and a.end_date:
                ax.axvspan(a.date, a.end_date, color=a.color, alpha=0.08)
                ax.text(a.date, 101, a.label, fontsize=8, color=a.color, va='bottom')

        # response_score-per-day lines, one per target — NaN gaps break the
        # line rather than connecting across them, matching connectNulls={false}.
        for i, (target_id, entry) in enumerate(sorted(by_target.items())):
            color = _GRAPH_PALETTE[i % len(_GRAPH_PALETTE)]
            ys = [entry['series'].get(d, float('nan')) for d in sorted_dates]
            ax.plot(sorted_dates, ys, marker='o', markersize=4, linewidth=2, color=color, label=entry['name'])

        for a in annotations:
            if a.annotation_type == 'phase_line':
                ax.axvline(x=a.date, color=a.color, linewidth=1.3, linestyle=_GRAPH_LINESTYLE.get(a.style, '-'))
                ax.text(a.date, 101, a.label, fontsize=8, color=a.color, ha='right', va='bottom', rotation=90)

        for a in annotations:
            if a.annotation_type == 'graph_note':
                ax.axvline(x=a.date, color='#94a3b8', linewidth=1, linestyle=':')
                ax.text(a.date, 101, '★', fontsize=9, color='#94a3b8', ha='center', va='bottom')

        ax.set_ylim(0, 100)
        ax.set_ylabel('% Correct')
        ax.yaxis.set_major_formatter(lambda v, _pos: f'{int(v)}%')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
        fig.autofmt_xdate()
        ax.grid(True, axis='y', color='#f1f5f9', linewidth=0.8)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        if by_target:
            ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.22),
                       ncol=min(len(by_target), 4), fontsize=9, frameon=False)

    ax.set_title(f'{program.name} — {date_from.isoformat()} to {date_to.isoformat()}', fontsize=12, loc='left')
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format=image_format, bbox_inches='tight')
    plt.close(fig)
    return buf.getvalue()


def _generate_graph(export_id: int, image_format: str) -> None:
    from .models import Export
    from apps.programs.models import Program

    export = Export.objects.get(id=export_id)
    try:
        _mark_processing(export)
        params = export.params

        program = Program.objects.get(id=params['program_id'])
        date_to = date.fromisoformat(params['date_to']) if params.get('date_to') else timezone.now().date()
        date_from = (
            date.fromisoformat(params['date_from']) if params.get('date_from')
            else date_to - timedelta(days=_DEFAULT_GRAPH_DAYS)
        )
        target_ids = params.get('target_ids') or []

        content = _render_program_graph(program, target_ids, date_from, date_to, image_format)
        filename = f'graph_{image_format}_{export_id}_{timezone.now():%Y%m%d_%H%M%S}.{image_format}'
        path, size = _save_file(content, filename)
        _mark_completed(export, path, size, 1)

    except Exception as exc:
        _mark_failed(export, str(exc))


def generate_graph_png(export_id: int) -> None:
    _generate_graph(export_id, 'png')


def generate_graph_svg(export_id: int) -> None:
    _generate_graph(export_id, 'svg')


# ---------------------------------------------------------------------------
# Note PDF — a single session note rendered as a standalone document
# ---------------------------------------------------------------------------

_REPORTLAB_ALLOWED_TAGS = {'b', 'i', 'u', 'br'}


def _html_to_reportlab_markup(html: str) -> str:
    """
    NoteEditor's contenteditable produces raw innerHTML (div/p/ul/li/b/i/u),
    already entity-escaped for any literal text it contains. reportlab's
    Paragraph only understands a small safe subset of markup and raises on
    an unrecognized tag — this maps the common contenteditable tags onto
    that subset instead of passing the HTML through unmodified.
    """
    import re
    text = re.sub(r'</(div|p)>', '<br/>', html, flags=re.IGNORECASE)
    text = re.sub(r'<(div|p)[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[^>]*>', '<br/>• ', text, flags=re.IGNORECASE)
    text = re.sub(r'</(li|ul|ol)>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<(ul|ol)[^>]*>', '', text, flags=re.IGNORECASE)

    def _keep_allowed(match: 're.Match') -> str:
        tag = match.group(1).lower().lstrip('/')
        return match.group(0) if tag in _REPORTLAB_ALLOWED_TAGS else ''

    text = re.sub(r'</?([a-zA-Z0-9]+)[^>]*/?>', _keep_allowed, text)
    return text.strip() or '—'


def _render_note_pdf(note) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('NoteTitle', parent=styles['Heading1'], fontSize=16, spaceAfter=4)
    meta_style = ParagraphStyle('Meta', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#64748b'))
    label_style = ParagraphStyle(
        'FieldLabel', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=10, textColor=colors.HexColor('#475569'), spaceBefore=12, spaceAfter=2,
    )
    value_style = ParagraphStyle('FieldValue', parent=styles['Normal'], fontSize=10.5, leading=14)

    story = [
        Paragraph(escape(note.template.name) if note.template else 'Session Note', title_style),
        Paragraph(
            escape(
                f'Client #{note.external_client_id}  ·  {note.note_date.strftime("%B %d, %Y")}  ·  '
                f'Staff: {note.staff.full_name if note.staff else "—"}  ·  '
                f'Status: {note.get_status_display()}'
            ),
            meta_style,
        ),
        Spacer(1, 14),
    ]

    body = note.body or {}
    field_defs = note.template.fields if note.template else []
    if field_defs:
        for field in field_defs:
            key = field.get('key')
            raw = body.get(key)
            if raw is None or raw == '':
                display = '—'
            elif isinstance(raw, list):
                display = ', '.join(str(v) for v in raw) or '—'
            elif isinstance(raw, bool):
                display = 'Yes' if raw else 'No'
            else:
                display = str(raw)
            # display comes from plain-text/number/select inputs, not HTML —
            # a user typing e.g. "5 < 10" would otherwise break reportlab's
            # strict XML-ish parser, so escape before adding the <br/> markup.
            story.append(Paragraph(escape(str(field.get('label', key))), label_style))
            story.append(Paragraph(escape(display).replace('\n', '<br/>'), value_style))
    elif isinstance(body.get('text'), str):
        # Free-form note — body.text is raw contenteditable innerHTML from
        # NoteEditor, already entity-escaped by the browser for any literal
        # text; map its tags onto reportlab's supported subset.
        story.append(Paragraph(_html_to_reportlab_markup(body['text']), value_style))
    else:
        for key, raw in body.items():
            story.append(Paragraph(escape(str(key)), label_style))
            story.append(Paragraph(escape(str(raw)), value_style))

    signatures = list(note.signatures.all())
    if signatures:
        story.append(Spacer(1, 18))
        story.append(Paragraph('Signatures', label_style))
        rows = [['Signer', 'Role', 'Type', 'Signed At']]
        for sig in signatures:
            rows.append([
                sig.signer_name, sig.signer_role, sig.get_signature_type_display(),
                sig.signed_at.strftime('%Y-%m-%d %H:%M UTC'),
            ])
        table = Table(rows, colWidths=[1.8 * inch, 1.4 * inch, 1.2 * inch, 1.8 * inch])
        table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#475569')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.HexColor('#cbd5e1')),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(table)

    if note.rejection_reason:
        story.append(Spacer(1, 14))
        story.append(Paragraph('Rejection reason', label_style))
        story.append(Paragraph(escape(note.rejection_reason).replace('\n', '<br/>'), value_style))

    doc.build(story)
    return buf.getvalue()


def generate_note_pdf(export_id: int) -> None:
    from .models import Export
    from apps.notes.models import LessonNote

    export = Export.objects.get(id=export_id)
    try:
        _mark_processing(export)
        params = export.params

        note = LessonNote.objects.select_related('staff', 'template').prefetch_related('signatures').get(
            id=params['note_id'],
        )

        content = _render_note_pdf(note)
        filename = f'note_pdf_{export_id}_{timezone.now():%Y%m%d_%H%M%S}.pdf'
        path, size = _save_file(content, filename)
        _mark_completed(export, path, size, 1)

    except Exception as exc:
        _mark_failed(export, str(exc))
