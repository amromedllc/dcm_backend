from ninja.errors import HttpError
from ninja.files import UploadedFile

IMAGE_CONTENT_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
VIDEO_CONTENT_TYPES = {'video/mp4', 'video/quicktime', 'video/webm'}

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 250 * 1024 * 1024


def validate_image_upload(file: UploadedFile, *, max_bytes: int = MAX_IMAGE_BYTES) -> None:
    if file.size > max_bytes:
        raise HttpError(400, f'Image must be under {max_bytes // (1024 * 1024)}MB')
    if file.content_type not in IMAGE_CONTENT_TYPES:
        raise HttpError(400, f'Image must be one of: {", ".join(sorted(IMAGE_CONTENT_TYPES))}')
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(file) as img:
            img.verify()
    except (UnidentifiedImageError, OSError):
        raise HttpError(400, 'File is not a valid image')
    finally:
        file.seek(0)


def validate_media_upload(file: UploadedFile, media_type: str) -> None:
    if media_type == 'photo':
        validate_image_upload(file, max_bytes=MAX_IMAGE_BYTES)
        return
    if media_type == 'video':
        if file.size > MAX_VIDEO_BYTES:
            raise HttpError(400, f'Video must be under {MAX_VIDEO_BYTES // (1024 * 1024)}MB')
        # No video-parsing dependency is available to verify actual content,
        # so this is limited to the browser-supplied content type + size cap.
        if file.content_type not in VIDEO_CONTENT_TYPES:
            raise HttpError(400, f'Video must be one of: {", ".join(sorted(VIDEO_CONTENT_TYPES))}')
        return
    raise HttpError(400, f'Unsupported media_type: {media_type}')
