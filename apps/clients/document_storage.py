"""Private document storage: never expose a media URL for client files."""
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, storages
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateDocumentStorage(FileSystemStorage):
    def __init__(self, **kwargs):
        base = Path(getattr(settings, 'BASE_DIR', Path(__file__).resolve().parents[2]))
        location = Path(getattr(settings, 'CLIENT_DOCUMENT_ROOT', base / 'private_documents'))
        # Fail closed if configured inside the publicly served media directory.
        public = Path(getattr(settings, 'MEDIA_ROOT', None) or base / 'media').resolve()
        if location.resolve().is_relative_to(public):
            raise ValueError('CLIENT_DOCUMENT_ROOT must be outside MEDIA_ROOT')
        super().__init__(location=location, file_permissions_mode=0o600, directory_permissions_mode=0o700, **kwargs)

    def url(self, name):
        raise NotImplementedError('Client documents require an authenticated download')


def get_document_storage():
    if 'client_documents' in settings.STORAGES:
        return storages['client_documents']
    return PrivateDocumentStorage()


def document_upload_path(instance, filename):
    import uuid
    suffix = Path(filename).suffix.lower()
    return f'{instance.organization_id}/{instance.client_id}/{uuid.uuid4().hex}{suffix}'
