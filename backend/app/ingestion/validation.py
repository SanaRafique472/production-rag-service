"""Upload validation: size, type, magic bytes, zip-bomb guard and hashing.

The file extension is only a claim. We confirm it by looking at the first
bytes of the file ("magic bytes") and, for Office files, at the container
contents. This blocks things like an executable renamed to ``invoice.pdf``.

Word files are identified by content, not only by extension, because users
often rename them: a legacy ``.doc`` saved as ``report.docx`` (or the other way
round) is read as what it really is. Password-protected Word files are
recognised and rejected with a clear message.
"""

import hashlib
import io
import re
import zipfile
from enum import StrEnum
from pathlib import PurePath

import olefile

from app.config import Settings
from app.errors import (
    CorruptFileError,
    EmptyFileError,
    EncryptedFileError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from app.ingestion.ooxml import main_document_part


class FileType(StrEnum):
    """File types the service accepts."""

    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"  # legacy Word 97-2003 binary (or RTF saved with a .doc name)
    RTF = "rtf"
    PPTX = "pptx"
    TXT = "txt"
    MD = "md"


EXTENSIONS: dict[str, FileType] = {
    ".pdf": FileType.PDF,
    ".docx": FileType.DOCX,
    ".doc": FileType.DOC,
    ".rtf": FileType.RTF,
    ".pptx": FileType.PPTX,
    ".txt": FileType.TXT,
    ".md": FileType.MD,
    ".markdown": FileType.MD,
}

# Signatures of binaries that must never be accepted as text.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # Compound File Binary: .doc and encrypted Office files
RTF_MAGIC = b"{\\rtf"
ZIP_MAGIC = b"PK\x03\x04"
_BINARY_SIGNATURES = (b"MZ", b"\x7fELF", b"%PDF", ZIP_MAGIC, b"\xca\xfe\xba\xbe", OLE_MAGIC)
_WORD_TYPES = (FileType.DOCX, FileType.DOC, FileType.RTF)
_PASSWORD_HINT = "Remove the password in Word (File > Info > Protect Document) and upload it again."


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 hash of the file content, used for duplicate detection."""
    return hashlib.sha256(data).hexdigest()


def safe_filename(name: str) -> str:
    """Strip directories and control characters from a user-supplied file name."""
    base = PurePath(name.replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:200] or "document"


def _check_zip(data: bytes, settings: Settings) -> zipfile.ZipFile:
    """Open an Office (ZIP) container and guard against zip bombs.

    The guard is on the *absolute* amount of data the archive expands to, not
    on the compression ratio of single members: ordinary Word files contain
    members that compress 100-1000x (tables with empty cells, BMP/EMF/TIFF
    images, repetitive XML), and rejecting those made many real DOCX uploads
    fail. A very high ratio is only treated as suspicious for large members.
    Declared sizes are enforced by ``zipfile`` while reading (CRC + length).
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError) as exc:
        raise CorruptFileError("The file is corrupt (damaged ZIP container).") from exc
    if len(infos) > settings.max_zip_members:
        raise CorruptFileError("File rejected: too many parts inside the document.", {"members": len(infos)})
    total = 0
    min_ratio_bytes = settings.zip_ratio_min_member_mb * 1024 * 1024
    for info in infos:
        total += info.file_size
        ratio = info.file_size / info.compress_size if info.compress_size else 0
        if info.file_size > min_ratio_bytes and ratio > settings.max_zip_ratio:
            raise CorruptFileError(
                "File rejected: suspicious compression ratio (possible zip bomb).", {"member": info.filename}
            )
    if total > settings.max_zip_uncompressed_mb * 1024 * 1024:
        raise CorruptFileError(
            "File rejected: uncompressed content is too large.",
            {"uncompressed_mb": round(total / 1024 / 1024), "limit_mb": settings.max_zip_uncompressed_mb},
        )
    return zf


def _ole_word_kind(data: bytes) -> FileType:
    """Classify a Compound File: legacy Word document, or encrypted (password-protected) Office file."""
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            if ole.exists("EncryptedPackage") or ole.exists("EncryptionInfo"):
                raise EncryptedFileError(f"This Word file is password-protected. {_PASSWORD_HINT}")
            if not ole.exists("WordDocument"):
                raise UnsupportedFileTypeError(
                    "File content is not a Word document (it looks like another Office format)."
                )
            fib = ole.openstream("WordDocument").read(12)
    except (OSError, ValueError) as exc:  # olefile raises OSError for damaged containers
        raise CorruptFileError("The DOC file is corrupt or unreadable.") from exc
    if len(fib) >= 12 and int.from_bytes(fib[10:12], "little") & 0x0100:  # FIB fEncrypted
        raise EncryptedFileError(f"This DOC file is password-protected. {_PASSWORD_HINT}")
    return FileType.DOC


def _word_kind(data: bytes, settings: Settings) -> FileType:
    """Identify a Word upload by its content: DOCX (ZIP), DOC (OLE) or RTF."""
    if data.startswith(ZIP_MAGIC):
        with _check_zip(data, settings) as zf:
            if main_document_part(zf) is None:
                raise UnsupportedFileTypeError("File content is not a valid Word document.")
        return FileType.DOCX
    if data.startswith(OLE_MAGIC):
        return _ole_word_kind(data)
    if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(RTF_MAGIC):
        return FileType.RTF
    head = data[:512].lstrip().lower()
    if head.startswith((b"<html", b"<!doctype html", b"mime-version")):
        raise UnsupportedFileTypeError(
            "This file is a web page saved with a Word extension. Open it in Word and save it as DOCX or PDF."
        )
    raise UnsupportedFileTypeError("File content is not a valid Word document.")


def detect_file_type(filename: str, data: bytes, settings: Settings) -> FileType:
    """Check size and type of an upload and return its verified ``FileType``.

    Raises:
        EmptyFileError: zero-byte file (400).
        FileTooLargeError: above the configured size limit (413).
        UnsupportedFileTypeError: unknown extension or mismatching content (415).
        CorruptFileError: damaged or suspicious container (400).
        EncryptedFileError: password-protected Word file (400).

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
    elif file_type in _WORD_TYPES:
        return _word_kind(data, settings)
    elif file_type is FileType.PPTX:
        if not data.startswith(ZIP_MAGIC):
            raise UnsupportedFileTypeError("File content is not a valid PPTX document.")
        with _check_zip(data, settings) as zf:
            if "ppt/presentation.xml" not in set(zf.namelist()):
                raise UnsupportedFileTypeError("File content is not a valid PPTX document.")
    else:  # TXT / MD must really be text
        if data.startswith(_BINARY_SIGNATURES) or b"\x00" in head:
            raise UnsupportedFileTypeError("File content is binary, not plain text.")
    return file_type
