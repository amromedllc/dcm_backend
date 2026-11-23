"""Small program-card images for local and remote Django storage backends."""
from io import BytesIO
import logging

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


def program_card_image_url(request, image_field):
    if not image_field:
        return None
    storage = image_field.storage
    name = f'{image_field.name}.card-v2.webp'
    try:
        if not storage.exists(name):
            with storage.open(image_field.name, 'rb') as source:
                with Image.open(source) as original:
                    image = ImageOps.exif_transpose(original)
                    image.thumbnail((800, 600), Image.Resampling.LANCZOS)
                    image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
                    output = BytesIO()
                    image.save(output, 'WEBP', quality=76, method=4)
            name = storage.save(name, ContentFile(output.getvalue()))
        # Obtain the variant's own URL: remote URLs may carry signed queries.
        return request.build_absolute_uri(storage.url(name))
    except Exception:
        # An optimization failure must not make the program unavailable.
        logger.warning('Could not prepare program card image %s', image_field.name, exc_info=True)
        return request.build_absolute_uri(image_field.url)
