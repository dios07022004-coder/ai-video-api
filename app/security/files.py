"""Upload validation + safe re-encoding.

Defense in depth against malicious uploads:
  1. size cap (streamed, before full read)
  2. declared MIME must be in the allowlist
  3. real content sniff via Pillow (magic bytes) — declared type can't lie
  4. decompression-bomb guard (MAX_IMAGE_PIXELS)
  5. full decode to prove the file is a real, non-corrupt image
  6. re-encode to a clean canonical image, stripping EXIF/metadata payloads
     ("virus-safe validation": the bytes that reach disk are our own re-encode,
     not the attacker's container)
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageFile, UnidentifiedImageError

from app.api.errors import UnsupportedMediaTypeError, UploadInvalidError, UploadTooLargeError
from app.config.settings import Settings

ImageFile.LOAD_TRUNCATED_IMAGES = False  # reject truncated files

# Map sniffed Pillow format → canonical output (ext, content-type)
_FORMAT_OUTPUT = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes            # clean re-encoded bytes
    ext: str
    content_type: str
    width: int
    height: int
    sha256: str


class ImageValidator:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def validate(self, raw: bytes, declared_type: str | None) -> ValidatedImage:
        # 1. size
        if len(raw) == 0:
            raise UploadInvalidError("Empty file.")
        if len(raw) > self._s.max_upload_bytes:
            raise UploadTooLargeError(
                details={"max_bytes": self._s.max_upload_bytes, "got_bytes": len(raw)}
            )

        # 2. declared MIME allowlist (advisory; real check is the sniff below)
        if declared_type and declared_type.lower() not in self._s.allowed_image_types_set:
            raise UnsupportedMediaTypeError(
                details={"declared": declared_type, "allowed": sorted(self._s.allowed_image_types_set)}
            )

        # 3-5. decompression-bomb guard + real decode
        Image.MAX_IMAGE_PIXELS = self._s.max_image_pixels
        try:
            with Image.open(io.BytesIO(raw)) as probe:
                fmt = (probe.format or "").upper()
                probe.verify()  # cheap structural check
        except Image.DecompressionBombError as exc:
            raise UploadInvalidError("Image exceeds pixel limit.", details={"error": str(exc)}) from exc
        except (UnidentifiedImageError, OSError) as exc:
            raise UploadInvalidError("File is not a valid, decodable image.", details={"error": str(exc)}) from exc

        if fmt not in _FORMAT_OUTPUT:
            raise UnsupportedMediaTypeError(details={"sniffed_format": fmt or "unknown"})

        # 6. full decode + clean re-encode (strip metadata)
        try:
            with Image.open(io.BytesIO(raw)) as img:
                img = img.convert("RGB") if fmt == "JPEG" else img.convert("RGBA") if fmt == "PNG" else img.convert("RGB")
                width, height = img.size
                out = io.BytesIO()
                ext, ctype = _FORMAT_OUTPUT[fmt]
                save_fmt = "JPEG" if fmt == "JPEG" else fmt
                params = {"quality": 92} if save_fmt == "JPEG" else {}
                img.save(out, format=save_fmt, **params)
                clean = out.getvalue()
        except OSError as exc:
            raise UploadInvalidError("Failed to re-encode image.", details={"error": str(exc)}) from exc

        return ValidatedImage(
            data=clean,
            ext=ext,
            content_type=ctype,
            width=width,
            height=height,
            sha256=hashlib.sha256(clean).hexdigest(),
        )
