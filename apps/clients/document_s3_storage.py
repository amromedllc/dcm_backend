"""Loaded only by installations configured with private S3 document storage."""
from storages.backends.s3boto3 import S3Boto3Storage


class PrivateDocumentS3Storage(S3Boto3Storage):
    location = 'private/client-documents'
    default_acl = 'private'
    file_overwrite = False
    custom_domain = None
    querystring_auth = True

    def url(self, name, **kwargs):
        raise NotImplementedError('Client documents require an authenticated download')
