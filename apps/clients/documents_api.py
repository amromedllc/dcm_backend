"""Client-scoped private uploads. No raw storage path or public URL is exposed."""
import logging
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from ninja import File, Form, Query, Router
from ninja.errors import HttpError
from ninja.files import UploadedFile

from apps.accounts.auth import jwt_auth
from apps.accounts.permissions import require_permission
from apps.audit.models import AuditLog
from .api import _get_client_or_404
from .document_schemas import DocumentOutput, DocumentPage, DocumentUpdate, FolderInput, FolderOutput
from .document_validation import validate_document
from .models import Client, ClientDocument, ClientDocumentFolder

router = Router(auth=jwt_auth)
logger = logging.getLogger(__name__)


def _access(request, client_id, extra=None):
    if request.user.role not in ('admin', 'supervisor', 'staff'):
        raise HttpError(403, 'Staff access required')
    require_permission(request, 'client_documents')
    if extra:
        require_permission(request, extra)
    return _get_client_or_404(request, client_id)


def _document(client, document_id):
    document = ClientDocument.objects.filter(client=client, pk=document_id).select_related('folder', 'created_by').first()
    if document is None:
        raise HttpError(404, 'Document not found')
    return document


def _folder(client, folder_id):
    if folder_id is None:
        return None
    folder = ClientDocumentFolder.objects.filter(client=client, pk=folder_id).first()
    if folder is None:
        raise HttpError(404, 'Folder not found')
    return folder


def _serialize(document):
    return {field: getattr(document, field) for field in (
        'id', 'client_id', 'title', 'category', 'folder_id', 'original_name', 'content_type',
        'size_bytes', 'created_at', 'updated_at', 'archived_at',
    )} | {'folder_name': document.folder.name if document.folder_id else None,
         'uploaded_by': document.created_by.full_name if document.created_by_id else 'Unknown'}


def _audit(request, obj, event):
    # Identifiers only: no filenames, titles, file bytes or patient names in audit metadata.
    AuditLog.objects.create(
        actor_id=request.user.pk, actor_email=request.user.email, actor_role=request.user.role,
        action='create' if event in ('uploaded', 'folder_created') else 'update',
        model=type(obj).__name__, object_id=str(obj.pk), object_repr=str(obj),
        changes={'event': event, 'client_id': obj.client_id},
        request_id=getattr(request, 'request_id', '') or '',
    )


@router.get('/{client_id}/document-folders', response=list[FolderOutput])
def folders(request, client_id: int):
    client = _access(request, client_id)
    return list(ClientDocumentFolder.objects.filter(client=client))


@router.post('/{client_id}/document-folders', response={201: FolderOutput})
@transaction.atomic
def create_folder(request, client_id: int, data: FolderInput):
    client = _access(request, client_id, 'client_documents_manage')
    if ClientDocumentFolder.objects.filter(client=client, name__iexact=data.name).exists():
        raise HttpError(409, 'A folder with this name already exists for this client')
    folder = ClientDocumentFolder.objects.create(client=client, name=data.name, created_by=request.user)
    _audit(request, folder, 'folder_created')
    return 201, folder


@router.patch('/{client_id}/document-folders/{folder_id}', response=FolderOutput)
@transaction.atomic
def rename_folder(request, client_id: int, folder_id: int, data: FolderInput):
    client = _access(request, client_id, 'client_documents_manage')
    folder = _folder(client, folder_id)
    if ClientDocumentFolder.objects.filter(client=client, name__iexact=data.name).exclude(pk=folder.pk).exists():
        raise HttpError(409, 'A folder with this name already exists for this client')
    folder.name = data.name
    folder.save(update_fields=['name', 'updated_at'])
    _audit(request, folder, 'folder_renamed')
    return folder


@router.get('/{client_id}/documents', response=DocumentPage)
def documents(request, client_id: int, search: str = '', category: str = '', folder_id: int | None = None,
              state: Literal['active', 'archived', 'all'] = 'active',
              offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    client = _access(request, client_id)
    if len(search) > 200:
        raise HttpError(400, 'Search must be at most 200 characters')
    if category and category not in ClientDocument.Category.values:
        raise HttpError(400, 'Invalid document category')
    qs = ClientDocument.objects.filter(client=client).select_related('folder', 'created_by')
    if state != 'all':
        qs = qs.filter(archived_at__isnull=state == 'active')
    if category:
        qs = qs.filter(category=category)
    if folder_id is not None:
        if folder_id == 0:
            qs = qs.filter(folder__isnull=True)
        else:
            _folder(client, folder_id)
            qs = qs.filter(folder_id=folder_id)
    if search.strip():
        qs = qs.filter(Q(title__icontains=search.strip()) | Q(original_name__icontains=search.strip()))
    return {'total': qs.count(), 'items': [_serialize(document) for document in qs[offset:offset + limit]]}


@router.post('/{client_id}/documents', response={200: DocumentOutput, 201: DocumentOutput})
def upload_document(request, client_id: int, file: UploadedFile = File(...), upload_id: UUID = Form(...),
                    title: str = Form(''), category: ClientDocument.Category = Form(ClientDocument.Category.OTHER),
                    folder_id: int | None = Form(None)):
    client = _access(request, client_id, 'client_documents_manage')
    folder = _folder(client, folder_id)
    original, content_type, size, digest = validate_document(file)
    title = title.strip() or PurePosixPath(original).stem[:200]
    if not title or len(title) > 200:
        raise HttpError(400, 'Title must contain 1–200 characters')
    document = None
    saved = False
    try:
        with transaction.atomic():
            # Serialize uploads per client to prevent concurrent retries leaving orphan blobs.
            Client.objects.select_for_update().get(pk=client.pk)
            existing = ClientDocument.objects.filter(client=client, upload_id=upload_id).select_related('folder', 'created_by').first()
            if existing:
                if existing.archived_at or any((existing.sha256 != digest, existing.title != title,
                                               existing.category != category, existing.folder_id != folder_id,
                                               existing.original_name != original)):
                    raise HttpError(409, 'This upload ID is already associated with a different document')
                return 200, _serialize(existing)
            document = ClientDocument(client=client, folder=folder, title=title, category=category,
                                      original_name=original, content_type=content_type, size_bytes=size,
                                      sha256=digest, upload_id=upload_id, created_by=request.user,
                                      organization_id=client.organization_id)
            document.file.save(original, file, save=False)
            saved = True
            document.save()
            _audit(request, document, 'uploaded')
            return 201, _serialize(document)
    except Exception:
        if saved:
            try:
                document.file.delete(save=False)  # Only the new, uncommitted blob.
            except Exception:
                logger.error('Could not clean up an uncommitted client document blob')
        raise


@router.patch('/{client_id}/documents/{document_id}', response=DocumentOutput)
@transaction.atomic
def update_document(request, client_id: int, document_id: int, data: DocumentUpdate):
    client = _access(request, client_id, 'client_documents_manage')
    document = _document(client, document_id)
    if document.archived_at:
        raise HttpError(409, 'Restore this document before editing it')
    document.folder = _folder(client, data.folder_id)
    document.title, document.category = data.title, data.category
    document.save(update_fields=['title', 'category', 'folder', 'updated_at'])
    _audit(request, document, 'metadata_updated')
    return _serialize(document)


@router.post('/{client_id}/documents/{document_id}/archive', response=DocumentOutput)
@transaction.atomic
def archive_document(request, client_id: int, document_id: int):
    client = _access(request, client_id, 'client_documents_archive')
    document = _document(client, document_id)
    if not document.archived_at:
        document.archived_at, document.archived_by = timezone.now(), request.user
        document.save(update_fields=['archived_at', 'archived_by', 'updated_at'])
        _audit(request, document, 'archived')
    return _serialize(document)


@router.post('/{client_id}/documents/{document_id}/restore', response=DocumentOutput)
@transaction.atomic
def restore_document(request, client_id: int, document_id: int):
    client = _access(request, client_id, 'client_documents_archive')
    document = _document(client, document_id)
    if document.archived_at:
        document.archived_at, document.archived_by = None, None
        document.save(update_fields=['archived_at', 'archived_by', 'updated_at'])
        _audit(request, document, 'restored')
    return _serialize(document)


@router.get('/{client_id}/documents/{document_id}/download')
def download_document(request, client_id: int, document_id: int):
    client = _access(request, client_id)
    document = _document(client, document_id)
    if document.archived_at:
        raise HttpError(409, 'Restore this document before downloading it')
    try:
        handle = document.file.open('rb')
    except FileNotFoundError:
        raise HttpError(404, 'Document file is unavailable')
    try:
        _audit(request, document, 'downloaded')
    except Exception:
        handle.close()
        raise
    response = FileResponse(handle, as_attachment=True, filename=document.original_name, content_type='application/octet-stream')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    response['Referrer-Policy'] = 'no-referrer'
    return response
