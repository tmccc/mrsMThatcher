"""Bound and fully decode native image containers without network or file I/O."""
from __future__ import annotations

import io
import warnings

from PIL import Image

MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_FRAMES = 32
MAX_TOTAL_FRAME_PIXELS = 32_000_000
_FORMATS = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/gif': 'GIF', 'image/webp': 'WEBP'}


def verify_complete_image(data: bytes, mime_type: str) -> None:
    """Verify container integrity and fully decode every bounded frame.

    Explicit terminators supplement decoders that tolerate missing final markers;
    Pillow's global truncation/decompression settings are never weakened.
    """
    if mime_type == 'image/jpeg' and not data.endswith(b'\xff\xd9'):
        raise ValueError('JPEG is truncated')
    if mime_type == 'image/png' and not data.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82'):
        raise ValueError('PNG is truncated')
    if mime_type == 'image/gif' and not data.endswith(b';'):
        raise ValueError('GIF is truncated')
    if mime_type == 'image/webp' and (len(data) < 12 or int.from_bytes(data[4:8], 'little') + 8 != len(data)):
        raise ValueError('WebP container length differs')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as picture:
            _validate_picture(picture, mime_type)
            picture.verify()
        with Image.open(io.BytesIO(data)) as picture:
            _validate_picture(picture, mime_type)
            frames = getattr(picture, 'n_frames', 1)
            if frames > MAX_IMAGE_FRAMES:
                raise ValueError('image frame limit exceeded')
            total_pixels = 0
            for frame in range(frames):
                picture.seek(frame)
                _validate_picture(picture, mime_type)
                total_pixels += picture.width * picture.height
                if total_pixels > MAX_TOTAL_FRAME_PIXELS:
                    raise ValueError('decoded image pixel budget exceeded')
                picture.load()


def _validate_picture(picture: Image.Image, mime_type: str) -> None:
    """Check decoder format and dimensions before allocating pixel buffers."""
    if picture.format != _FORMATS.get(mime_type):
        raise ValueError('image MIME type does not match decoded format')
    if (not 1 <= picture.width <= MAX_IMAGE_DIMENSION
            or not 1 <= picture.height <= MAX_IMAGE_DIMENSION
            or picture.width * picture.height > MAX_IMAGE_PIXELS):
        raise ValueError('image dimensions exceed safe bound')
