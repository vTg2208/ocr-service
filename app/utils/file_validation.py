"""
File validation utilities.

Centralizes every safety check performed on an incoming upload:
extension whitelisting, size limits, emptiness, actual content-type
sniffing (so a renamed .exe can't slip through as ".jpg"), and filename
sanitization to prevent path traversal.
"""

import os
import re
from dataclasses import dataclass

import filetype

from app.config import get_settings

settings = get_settings()

# Maps the real, sniffed MIME type to the extensions we accept for it.
# This is what actually protects us from spoofed extensions.
_ALLOWED_MIME_EXTENSIONS = {
    "image/jpeg": {"jpg", "jpeg"},
    "image/png": {"png"},
    "image/bmp": {"bmp"},
    "image/tiff": {"tif", "tiff"},
    "application/pdf": {"pdf"},
}


class FileValidationError(Exception):
    """Raised when an uploaded file fails validation. Maps to HTTP 400."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass
class ValidatedFile:
    safe_filename: str
    extension: str
    content: bytes
    is_pdf: bool


def _sanitize_filename(filename: str) -> str:
    """Strip any path components and disallow traversal characters."""
    filename = os.path.basename(filename or "upload")
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    return filename or "upload"


def _get_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def validate_upload(filename: str, content: bytes) -> ValidatedFile:
    """
    Run all validation checks on an uploaded file's raw bytes.

    Raises FileValidationError with a user-facing message on any failure.
    Returns a ValidatedFile with a sanitized filename on success.
    """
    safe_filename = _sanitize_filename(filename)
    extension = _get_extension(safe_filename)

    if not content:
        raise FileValidationError("Uploaded file is empty.")

    if len(content) > settings.max_file_size_bytes:
        raise FileValidationError(
            f"File exceeds maximum allowed size of {settings.max_file_size_mb} MB."
        )

    if extension not in settings.allowed_extensions:
        raise FileValidationError("Unsupported file type.")

    # Sniff the actual content — do not trust the extension alone.
    kind = filetype.guess(content)
    if kind is None:
        raise FileValidationError("Could not determine file type. File may be corrupted.")

    allowed_exts_for_mime = _ALLOWED_MIME_EXTENSIONS.get(kind.mime)
    if allowed_exts_for_mime is None or extension not in allowed_exts_for_mime:
        raise FileValidationError(
            "File content does not match its extension, or file type is unsupported."
        )

    return ValidatedFile(
        safe_filename=safe_filename,
        extension=extension,
        content=content,
        is_pdf=(kind.mime == "application/pdf"),
    )
