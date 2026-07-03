from django.utils import timezone
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import jwt_auth
from .models import Export
from .schemas import ExportSchema, ExportCreateRequest, ExportDownloadResponse

router = Router(auth=jwt_auth)

# Map export_type → Celery task function
_TASK_MAP = {
    'trial_csv': 'apps.exports.tasks.generate_trial_csv',
    'behavior_csv': 'apps.exports.tasks.generate_behavior_csv',
    'abc_csv': 'apps.exports.tasks.generate_abc_csv',
    'raw_zip': 'apps.exports.tasks.generate_raw_zip',
}

_VALID_TYPES = set(_TASK_MAP.keys())


def _get_download_url(export: Export) -> str:
    """
    Returns a URL for downloading the export file.
    With django-storages + S3, default_storage.url() returns a pre-signed URL.
    In local dev, it returns a /media/ path served by Django.
    """
    from django.core.files.storage import default_storage
    return default_storage.url(export.file_path)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

@router.post('/exports', response={202: ExportSchema})
def request_export(request, data: ExportCreateRequest):
    """
    Enqueues an async export job. Returns immediately with status=pending.
    Poll GET /exports/{id} to check when it's ready.
    """
    if data.export_type not in _VALID_TYPES:
        raise HttpError(400, f'Invalid export_type. Valid: {", ".join(sorted(_VALID_TYPES))}')

    # Basic param validation per type
    if data.export_type in ('trial_csv', 'behavior_csv', 'raw_zip') and not data.program_id:
        raise HttpError(400, f'{data.export_type} requires program_id')
    if data.export_type == 'abc_csv' and not data.client_id:
        raise HttpError(400, 'abc_csv requires client_id')

    params = data.dict(exclude={'export_type'}, exclude_none=True)

    export = Export.objects.create(
        export_type=data.export_type,
        params=params,
        created_by=request.user,
    )

    # Dispatch Celery task
    from celery import current_app
    current_app.send_task(_TASK_MAP[data.export_type], args=[export.id])

    return 202, export


@router.get('/exports', response=list[ExportSchema])
def list_exports(request, export_type: str | None = None, status: str | None = None):
    qs = Export.objects.filter(created_by=request.user)
    if export_type:
        qs = qs.filter(export_type=export_type)
    if status:
        qs = qs.filter(status=status)
    return list(qs)


@router.get('/exports/{export_id}', response=ExportSchema)
def get_export(request, export_id: int):
    try:
        return Export.objects.get(id=export_id, created_by=request.user)
    except Export.DoesNotExist:
        raise HttpError(404, 'Export not found')


@router.get('/exports/{export_id}/download', response=ExportDownloadResponse)
def download_export(request, export_id: int):
    """
    Returns a download URL for a completed export.
    In production (S3), this is a pre-signed URL valid for 1 hour.
    In local dev, it's a /media/ URL served directly by Django.
    """
    try:
        export = Export.objects.get(id=export_id, created_by=request.user)
    except Export.DoesNotExist:
        raise HttpError(404, 'Export not found')

    if export.status != Export.Status.COMPLETED:
        raise HttpError(409, f'Export is {export.status} — not available for download yet')

    if export.expires_at and export.expires_at < timezone.now():
        raise HttpError(410, 'This export has expired and must be regenerated')

    # Increment download counter
    export.download_count += 1
    export.last_downloaded_at = timezone.now()
    export.save(update_fields=['download_count', 'last_downloaded_at'])

    return ExportDownloadResponse(
        export_id=export.id,
        download_url=_get_download_url(export),
        expires_in_seconds=3600,
    )


@router.delete('/exports/{export_id}', response={204: None})
def delete_export(request, export_id: int):
    """Remove the Export record. Does not delete the underlying file from storage."""
    try:
        Export.objects.get(id=export_id, created_by=request.user).delete()
    except Export.DoesNotExist:
        raise HttpError(404, 'Export not found')
    return 204, None
