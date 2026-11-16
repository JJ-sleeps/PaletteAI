"""Extract-Transform-Load for uploaded images.

validate -> normalise (orientation, first frame, transparency, resize) ->
sample opaque pixels -> hand Lab pixels to the clustering step.
Anything that fails validation raises ``IngestError`` and never reaches the DB.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
ALLOWED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif", "BMP": "image/bmp"}
ANALYSIS_SIDE = 200      # longest side used for pixel sampling
DISPLAY_SIDE = 640       # longest side of the stored preview
MAX_SAMPLES = 5000
ALPHA_THRESHOLD = 128    # pixels more transparent than this are ignored

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class IngestError(Exception):
    """A rejected upload. ``status`` is the HTTP status the API should return."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass
class NormalisedImage:
    content_hash: str
    filename: str
    mime: str
    width: int
    height: int
    size_bytes: int
    has_alpha: bool
    transparent_fraction: float
    pixels: np.ndarray        # (n, 3) uint8 sRGB sample of opaque pixels
    preview_jpeg: bytes       # normalised preview, transparency flattened on white


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalise_image(data: bytes, filename: str = "upload") -> NormalisedImage:
    if not data:
        raise IngestError("empty_file", "The uploaded file is empty.", 400)
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(
            "file_too_large",
            f"The file is {len(data) / 1_048_576:.1f} MB; the limit is {MAX_UPLOAD_BYTES // 1_048_576} MB.",
            413,
        )
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()                      # integrity check (consumes the object)
        if fmt not in ALLOWED_FORMATS:
            raise IngestError(
                "unsupported_type",
                f"{fmt or 'This file type'} is not supported. Upload a PNG, JPEG, WebP, GIF or BMP image.",
                415,
            )
        im = Image.open(io.BytesIO(data))
        im.seek(0)                              # first frame of animations
        im = ImageOps.exif_transpose(im)
        width, height = im.size
        has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        rgba = im.convert("RGBA")
    except IngestError:
        raise
    except Image.DecompressionBombError:
        raise IngestError("image_too_large", "The image has too many pixels to process safely.", 413) from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise IngestError(
            "invalid_image",
            "The file could not be read as an image. It may be corrupt or not an image at all.",
            415,
        ) from None

    digest = content_hash(data)

    # NEAREST keeps true pixel colours (no blended edge colours enter the sample).
    analysis = rgba.copy()
    analysis.thumbnail((ANALYSIS_SIDE, ANALYSIS_SIDE), Image.Resampling.NEAREST)
    arr = np.asarray(analysis).reshape(-1, 4)
    opaque = arr[arr[:, 3] >= ALPHA_THRESHOLD][:, :3]
    if len(opaque) == 0:
        raise IngestError("fully_transparent", "The image is fully transparent, so there are no colours to extract.")
    transparent_fraction = 1.0 - len(opaque) / len(arr)
    if len(opaque) > MAX_SAMPLES:
        rng = np.random.default_rng(int(digest[:8], 16))   # deterministic per file
        opaque = opaque[rng.choice(len(opaque), MAX_SAMPLES, replace=False)]

    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    flat.thumbnail((DISPLAY_SIDE, DISPLAY_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    flat.save(buf, "JPEG", quality=90)

    return NormalisedImage(
        content_hash=digest,
        filename=(filename or "upload")[:200],
        mime=ALLOWED_FORMATS[fmt],
        width=width,
        height=height,
        size_bytes=len(data),
        has_alpha=has_alpha,
        transparent_fraction=round(transparent_fraction, 4),
        pixels=opaque,
        preview_jpeg=buf.getvalue(),
    )
