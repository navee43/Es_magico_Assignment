"""Upload validation: magic-byte signatures, size limits and filename sanitisation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePath


class DocumentError(Exception):
    """A document could not be processed. ``code`` is a stable machine-readable id."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FileInfo:
    kind: str  # "pdf" | "image"
    mime_type: str
    extension: str
    size_bytes: int
    sha256: str


_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image", "image/png", ".png"),
    (b"\xff\xd8\xff", "image", "image/jpeg", ".jpg"),
)


def detect_file_type(data: bytes) -> tuple[str, str, str] | None:
    """Identify the file by its content (never by its extension)."""
    for magic, kind, mime, ext in _SIGNATURES:
        if data.startswith(magic):
            return kind, mime, ext
    # The PDF header may be preceded by a little garbage; spec allows it within 1 KB.
    if b"%PDF-" in data[:1024]:
        return "pdf", "application/pdf", ".pdf"
    return None


def validate_bytes(data: bytes, max_bytes: int) -> FileInfo:
    if not data:
        raise DocumentError("EMPTY_FILE", "The uploaded file is empty.")
    if len(data) > max_bytes:
        raise DocumentError(
            "FILE_TOO_LARGE",
            f"File is {len(data) / 1_048_576:.1f} MB; the limit is {max_bytes / 1_048_576:.0f} MB.",
        )
    detected = detect_file_type(data)
    if detected is None:
        raise DocumentError(
            "UNSUPPORTED_FILE_TYPE", "Only PDF, JPEG and PNG documents are supported."
        )
    kind, mime, ext = detected
    return FileInfo(
        kind=kind,
        mime_type=mime,
        extension=ext,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._ \-()]+")


def sanitize_filename(name: str | None, default: str = "document") -> str:
    """Return a display-safe base name. Stored files never use this name on disk."""
    if not name:
        return default
    base = PurePath(name.replace("\\", "/")).name
    base = _SAFE_CHARS.sub("_", base).strip(" .")
    base = re.sub(r"_+", "_", base)
    if not base:
        return default
    if len(base) > 120:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 120 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 8 else base[:120]
    return base
