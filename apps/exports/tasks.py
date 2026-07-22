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
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from shared.audit import log_export


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
