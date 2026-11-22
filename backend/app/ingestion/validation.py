"""Upload validation: size, type, magic bytes, zip-bomb guard and hashing.

The file extension is only a claim. We confirm it by looking at the first
bytes of the file ("magic bytes") and, for Office files, at the ZIP
contents. This blocks things like an executable renamed to ``invoice.pdf``.
"""

import hashlib
import io
import re
import zipfile
from enum import StrEnum
from pathlib import PurePath

from app.config import Settings
from app.errors import (
    CorruptFileError,
    EmptyFileError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)


class FileType(StrEnum):
    """File types the service accepts."""

    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    TXT = "txt"
    MD = "md"


EXTENSIONS: dict[str, FileType] = {
    ".pdf": FileType.PDF,
    ".docx": FileType.DOCX,
    ".pptx": FileType.PPTX,
    ".txt": FileType.TXT,
    ".md": FileType.MD,
    ".markdown": FileType.MD,
}

# Signatures of binaries that must never be accepted as text.
_BINARY_SIGNATURES = (b"MZ", b"\x7fELF", b"%PDF", b"PK\x03\x04", b"\xca\xfe\xba\xbe")
_OFFICE_MARKERS = {FileType.DOCX: "word/document.xml", FileType.PPTX: "ppt/presentation.xml"}


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 hash of the file content, used for duplicate detection."""
    return hashlib.sha256(data).hexdigest()


def safe_filename(name: str) -> str:
    """Strip directories and control characters from a user-supplied file name."""
    base = PurePath(name.replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:200] or "document"


def _check_zip(data: bytes, file_type: FileType, settings: Settings) -> None:
    """Validate an Office (ZIP) container and guard against zip bombs."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if _OFFICE_MARKERS[file_type] not in names:
                raise UnsupportedFileTypeError(f"File content is not a valid {file_type.value.upper()} document.")
            total = 0
            for info in zf.infolist():
                total += info.file_size
                if info.compress_size and info.file_size / info.compress_size > settings.max_zip_ratio:
                    raise CorruptFileError(
                        "File rejected: suspicious compression ratio (possible zip bomb).",
                        {"member": info.filename},
                    )
            if total > settings.max_zip_uncompressed_mb * 1024 * 1024:
                raise CorruptFileError("File rejected: uncompressed content is too large.")
    except zipfile.BadZipFile as exc:
        raise CorruptFileError(f"The {file_type.value.upper()} file is corrupt.") from exc


def detect_file_type(filename: str, data: bytes, settings: Settings) -> FileType:
    """Check size and type of an upload and return its verified ``FileType``.

    Raises:
        EmptyFileError: zero-byte file (400).
        FileTooLargeError: above the configured size limit (413).
        UnsupportedFileTypeError: unknown extension or mismatching content (415).
        CorruptFileError: damaged or suspicious container (400).

    """
    if not data:
        raise EmptyFileError("The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise FileTooLargeError(
            f"File is larger than the {settings.max_upload_mb} MB limit.",
            {"size_bytes": len(data), "limit_bytes": settings.max_upload_bytes},
        )

    ext = PurePath(filename).suffix.lower()
    file_type = EXTENSIONS.get(ext)
    if file_type is None:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{ext or 'none'}'.",
            {"supported": sorted(EXTENSIONS)},
        )

    head = data[:1024]
    if file_type is FileType.PDF:
        if b"%PDF-" not in head:
            raise UnsupportedFileTypeError("File content is not a valid PDF.")
    elif file_type in _OFFICE_MARKERS:
        if not data.startswith(b"PK\x03\x04"):
            raise UnsupportedFileTypeError(f"File content is not a valid {file_type.value.upper()} document.")
        _check_zip(data, file_type, settings)
    else:  # TXT / MD must really be text
        if data.startswith(_BINARY_SIGNATURES) or b"\x00" in head:
            raise UnsupportedFileTypeError("File content is binary, not plain text.")
    return file_type
