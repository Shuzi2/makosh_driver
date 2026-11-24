"""Private previews for previously stored iPhone multi-picture JPEG files."""
from io import BytesIO
from pathlib import Path
import warnings

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError


def is_mpo_document(doc):
    if doc.content_type != 'application/octet-stream' or Path(doc.original_name).suffix.lower() not in ('.jpg', '.jpeg', '.mpo'):
        return False
    if not doc.file or doc.size > settings.MAX_DOCUMENT_BYTES:
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with doc.file.open('rb') as stream, Image.open(stream) as image:
                if image.format != 'MPO':
                    return False
                image.verify()
                return True
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return False


def mpo_preview(doc):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with doc.file.open('rb') as stream, Image.open(stream) as image:
            if image.format != 'MPO':
                raise ValueError('Not an MPO image')
            image.seek(0)
            converted=ImageOps.exif_transpose(image).convert('RGB')
            output=BytesIO()
            converted.save(output,format='JPEG',quality=90)
            output.seek(0)
            return output
