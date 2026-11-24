"""Ingestion pipeline: bytes in, validated and chunked document out.

``process_file`` is pure CPU work (no network), so the API runs it in a worker
thread with a timeout. Embedding and storage are added in Phase 2.
"""

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.errors import NoExtractableTextError
from app.ingestion.chunking import PROPOSED, Chunk, ChunkingConfig, chunk_document
from app.ingestion.cleaning import clean_units
from app.ingestion.extractors.base import ExtractedDocument, TextUnit
from app.ingestion.extractors.legacy_doc import extract_doc, extract_rtf
from app.ingestion.extractors.office import extract_docx, extract_pptx
from app.ingestion.extractors.pdf import extract_pdf
from app.ingestion.extractors.text import extract_text
from app.ingestion.validation import FileType, detect_file_type, safe_filename, sha256_hex


@dataclass
class IngestionResult:
    """Everything produced from one uploaded file before embedding."""

    filename: str
    file_type: FileType
    sha256: str
    size_bytes: int
    page_count: int | None
    empty_pages: list[int]
    units: list[TextUnit]
    chunks: list[Chunk]


def extract(file_type: FileType, data: bytes, settings: Settings) -> ExtractedDocument:
    """Dispatch to the extractor for ``file_type``."""
    if file_type is FileType.PDF:
        return extract_pdf(data, settings.max_pages)
    if file_type is FileType.DOCX:
        return extract_docx(data)
    if file_type is FileType.DOC:
        return extract_doc(data, settings)
    if file_type is FileType.RTF:
        return extract_rtf(data, settings)
    if file_type is FileType.PPTX:
        return extract_pptx(data, settings.max_pages)
    return extract_text(data, markdown=file_type is FileType.MD)


def process_file(
    filename: str,
    data: bytes,
    *,
    document_id: str,
    config: ChunkingConfig = PROPOSED,
    settings: Settings | None = None,
) -> IngestionResult:
    """Validate, extract, clean and chunk one file.

    Raises the typed errors from ``app.errors`` (400/413/415) on bad input.
    """
    settings = settings or get_settings()
    name = safe_filename(filename)
    file_type = detect_file_type(name, data, settings)
    extracted = extract(file_type, data, settings)
    units = clean_units(extracted.units, extracted.page_count)

    total_chars = sum(len(u.text) for u in units)
    if total_chars < settings.min_chars_per_page * max(extracted.page_count or 1, 1):
        raise NoExtractableTextError(
            "No extractable text found. The file may be scanned images or empty.",
            {"page_count": extracted.page_count, "characters": total_chars},
        )

    chunks = chunk_document(units, document_id=document_id, filename=name, config=config)
    return IngestionResult(
        filename=name,
        file_type=file_type,
        sha256=sha256_hex(data),
        size_bytes=len(data),
        page_count=extracted.page_count,
        empty_pages=extracted.empty_pages,
        units=units,
        chunks=chunks,
    )
