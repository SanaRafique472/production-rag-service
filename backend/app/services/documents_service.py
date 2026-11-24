"""Document use cases: upload (ingest), list, get, content, reload and delete.

Upload order matters for cost and safety:
1. cheap checks first (type, size, magic bytes) -> 400/413/415;
2. duplicate check by SHA-256 before any paid embedding call -> 409;
3. CPU-heavy extraction in a worker thread with a timeout;
4. embeddings (parallel batches), then DB rows and Qdrant points;
5. if storing vectors fails, the DB transaction is rolled back and any partial
   points are removed, so a document is never half-stored.

The original bytes are kept (``document_files``) so a document can be
**reloaded** later without uploading it again: reload re-extracts the stored
file with the current extractors, re-embeds it and swaps the index in place.
Documents uploaded before files were stored are re-indexed from their stored
chunk text instead. A failed reload never removes a working index.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.sparse import doc_sparse
from app.db.models import ChunkRow, Document, DocumentFile
from app.errors import (
    AppError,
    CorruptFileError,
    DuplicateDocumentError,
    NotFoundError,
    UpstreamUnavailableError,
)
from app.guardrails import scan_document_text
from app.ingestion.chunking import Chunk
from app.ingestion.pipeline import IngestionResult, process_file
from app.ingestion.validation import EXTENSIONS, detect_file_type, safe_filename, sha256_hex
from app.schemas import ContentBlock, DocumentOut
from app.services.container import Container

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- helpers


async def _extract(c: Container, doc_id: str, name: str, data: bytes) -> IngestionResult:
    """Run the CPU-bound pipeline in a worker thread, with the ingestion timeout."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(process_file, name, data, document_id=doc_id, settings=c.settings),
            c.settings.ingestion_timeout_s,
        )
    except TimeoutError as exc:
        raise CorruptFileError(
            f"The document took longer than {int(c.settings.ingestion_timeout_s)} s to process and was rejected."
        ) from exc


def _chunk_rows(chunks: list[Chunk], flags: list[bool], doc_id: str, user_id: str) -> list[ChunkRow]:
    return [
        ChunkRow(
            id=ch.id,
            document_id=doc_id,
            user_id=user_id,
            position=ch.position,
            page_start=ch.page_start,
            page_end=ch.page_end,
            section=ch.section,
            content=ch.text,
            token_count=ch.token_count,
            kind=ch.kind,
            chunking_config=ch.chunking_config,
            suspicious=flag,
        )
        for ch, flag in zip(chunks, flags, strict=True)
    ]


async def _file_info(db: AsyncSession, doc_ids: list[str]) -> dict[str, str | None]:
    """Map document id -> last processing error, for documents whose original file is stored."""
    if not doc_ids:
        return {}
    rows = await db.execute(
        select(DocumentFile.document_id, DocumentFile.last_error).where(DocumentFile.document_id.in_(doc_ids))
    )
    return {doc_id: err for doc_id, err in rows.all()}


async def to_out(db: AsyncSession, docs: list[Document]) -> list[DocumentOut]:
    """API view of documents, including whether they can be re-extracted and their last error."""
    info = await _file_info(db, [d.id for d in docs])
    return [
        DocumentOut.model_validate(d).model_copy(update={"can_reprocess": d.id in info, "last_error": info.get(d.id)})
        for d in docs
    ]


# ---------------------------------------------------------------- upload


async def upload_document(db: AsyncSession, c: Container, user_id: str, filename: str, data: bytes) -> Document:
    """Validate, ingest, embed and store one file for ``user_id``."""
    s = c.settings
    name = safe_filename(filename)
    try:
        detect_file_type(name, data, s)
    except AppError as exc:
        log.info("upload rejected", extra={"error_type": exc.code, "user_id": user_id})
        raise
    digest = sha256_hex(data)
    existing = await db.scalar(select(Document).where(Document.user_id == user_id, Document.sha256 == digest))
    if existing is not None:
        raise DuplicateDocumentError(
            f"This file was already uploaded as {existing.filename}.",
            {"existing_document_id": existing.id, "filename": existing.filename},
        )

    doc_id = str(uuid.uuid4())
    try:
        result = await _extract(c, doc_id, name, data)
    except AppError as exc:
        log.info("upload rejected during extraction", extra={"error_type": exc.code, "user_id": user_id})
        raise

    chunks = result.chunks
    flags = [scan_document_text(ch.text) for ch in chunks]
    dense = await c.embedder.embed_documents([ch.embed_text for ch in chunks])
    sparse = [doc_sparse(ch.embed_text) for ch in chunks]

    doc = Document(
        id=doc_id,
        user_id=user_id,
        filename=result.filename,
        file_type=result.file_type.value,
        size_bytes=result.size_bytes,
        sha256=digest,
        page_count=result.page_count,
        empty_pages=result.empty_pages,
        chunk_count=len(chunks),
        flagged_chunks=sum(flags),
        status="processing",
    )
    db.add(doc)
    db.add_all(_chunk_rows(chunks, flags, doc_id, user_id))
    if s.store_original_files:
        db.add(DocumentFile(document_id=doc_id, user_id=user_id, data=data))
    try:
        await db.flush()
        await c.store.ensure_collection()
        await c.store.upsert(chunks, dense, sparse, user_id, flags)
        doc.status = "ready"
        await db.commit()
    except IntegrityError as exc:  # same file uploaded concurrently
        await db.rollback()
        raise DuplicateDocumentError("This file was already uploaded.") from exc
    except Exception:
        await db.rollback()
        try:
            await c.store.delete_document(user_id, doc_id)
        except Exception:  # best effort cleanup; original error matters more
            log.warning("cleanup after failed upload did not finish", extra={"document_id": doc_id})
        raise
    log.info(
        "document ingested",
        extra={"document_id": doc_id, "user_id": user_id, "file_type": doc.file_type, "chunks": len(chunks)},
    )
    return doc


# ---------------------------------------------------------------- read


async def list_documents(db: AsyncSession, user_id: str, limit: int, offset: int) -> tuple[list[Document], int]:
    """Return one page of the user's documents (newest first) and the total count."""
    total = await db.scalar(select(func.count()).select_from(Document).where(Document.user_id == user_id)) or 0
    rows = await db.scalars(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows), int(total)


async def get_document(db: AsyncSession, user_id: str, document_id: str) -> Document:
    """Fetch an owned document; other users' documents look like 404 (not 403)."""
    doc = await db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user_id))
    if doc is None:
        raise NotFoundError("Document not found.", {"document_id": document_id})
    return doc


def _strip_overlap(previous: str, current: str, max_chars: int = 1500) -> str:
    """Drop the sentences a chunk repeats from the end of the previous chunk (chunking overlap)."""
    best = 0
    limit = min(len(previous), len(current), max_chars)
    for i in range(1, limit + 1):
        if current[i - 1] in " \n" or i == limit:
            head = current[:i].rstrip()
            if head and previous.endswith(head):
                best = i
    return current[best:].lstrip() if best else current


async def get_content(
    db: AsyncSession, user_id: str, document_id: str, offset: int, limit: int
) -> tuple[Document, list[ContentBlock], int, int]:
    """The document's extracted text in reading order (chunk overlap removed for display)."""
    doc = await get_document(db, user_id, document_id)
    base = select(ChunkRow).where(ChunkRow.document_id == doc.id, ChunkRow.user_id == user_id)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    tokens = (
        await db.scalar(
            select(func.coalesce(func.sum(ChunkRow.token_count), 0)).where(
                ChunkRow.document_id == doc.id, ChunkRow.user_id == user_id
            )
        )
        or 0
    )
    start = max(offset - 1, 0)  # one extra row so overlap can be removed from the first block too
    rows = list(await db.scalars(base.order_by(ChunkRow.position).offset(start).limit(limit + (offset - start))))
    blocks: list[ContentBlock] = []
    prev: ChunkRow | None = None
    for row in rows:
        text = row.content
        if prev is not None and prev.kind == row.kind == "text" and prev.section == row.section:
            text = _strip_overlap(prev.content, text)
        if row.position >= offset:
            blocks.append(
                ContentBlock(
                    position=row.position,
                    section=row.section,
                    page_start=row.page_start,
                    page_end=row.page_end,
                    kind=row.kind,
                    text=text,
                )
            )
        prev = row
    return doc, blocks, int(total), int(tokens)


# ---------------------------------------------------------------- reload


async def _stored_chunks(db: AsyncSession, doc: Document) -> tuple[list[Chunk], list[bool]]:
    """Rebuild ``Chunk`` objects from the stored rows (for documents without a stored file)."""
    rows = list(await db.scalars(select(ChunkRow).where(ChunkRow.document_id == doc.id).order_by(ChunkRow.position)))
    chunks = []
    for r in rows:
        header = f"{doc.filename} > {r.section}" if r.section else doc.filename
        chunks.append(
            Chunk(
                id=r.id,
                document_id=doc.id,
                filename=doc.filename,
                position=r.position,
                text=r.content,
                embed_text=f"{header}\n\n{r.content}",
                page_start=r.page_start,
                page_end=r.page_end,
                section=r.section,
                token_count=r.token_count,
                kind=r.kind,  # type: ignore[arg-type]
                chunking_config=r.chunking_config,
            )
        )
    return chunks, [r.suspicious for r in rows]


async def reload_document(db: AsyncSession, c: Container, user_id: str, document_id: str) -> tuple[Document, str, int]:
    """Re-process a document without a new upload. Returns (document, mode, previous chunk count).

    * ``reextracted``: the stored original file is extracted again with the current
      extractors, re-embedded, and the index is swapped in place;
    * ``reindexed``: no stored file (uploaded before files were kept), so the stored
      chunk text is re-embedded and re-indexed (repairs a lost or stale vector index).

    Everything that can fail (extraction, embeddings) runs before anything is changed,
    so a failed reload leaves the existing, working index untouched.
    """
    doc = await get_document(db, user_id, document_id)
    previous = doc.chunk_count
    stored = await db.get(DocumentFile, doc.id)
    old_ids = set(await db.scalars(select(ChunkRow.id).where(ChunkRow.document_id == doc.id)))

    result: IngestionResult | None = None
    if stored is not None:
        mode = "reextracted"
        try:
            result = await _extract(c, doc.id, doc.filename, stored.data)
        except AppError as exc:
            stored.last_error = exc.message
            await db.commit()
            log.warning("reload failed during extraction", extra={"document_id": doc.id, "error_type": exc.code})
            raise
        chunks = result.chunks
        flags = [scan_document_text(ch.text) for ch in chunks]
    else:
        mode = "reindexed"
        chunks, flags = await _stored_chunks(db, doc)
        if not chunks:
            raise NotFoundError("This document has no stored content to reload. Upload the file again.")

    dense = await c.embedder.embed_documents([ch.embed_text for ch in chunks])
    sparse = [doc_sparse(ch.embed_text) for ch in chunks]

    try:
        await c.store.ensure_collection()
        await c.store.upsert(chunks, dense, sparse, user_id, flags)  # same ids are overwritten in place
        stale = sorted(old_ids - {ch.id for ch in chunks})
        if stale:
            await c.store.delete_points(stale)
        if result is not None:
            await db.execute(delete(ChunkRow).where(ChunkRow.document_id == doc.id))
            db.add_all(_chunk_rows(chunks, flags, doc.id, user_id))
            doc.file_type = result.file_type.value
            doc.page_count = result.page_count
            doc.empty_pages = result.empty_pages
        doc.chunk_count = len(chunks)
        doc.flagged_chunks = sum(flags)
        doc.status = "ready"
        if stored is not None:
            stored.last_error = None
            stored.processed_at = datetime.now(UTC)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        doc = await get_document(db, user_id, document_id)
        doc.status = "failed"  # the vector index may be partly updated; another reload repairs it
        stored = await db.get(DocumentFile, doc.id)
        if stored is not None:
            stored.last_error = "Storing the re-processed document failed. Reload it again."
        await db.commit()
        log.exception("reload failed while storing", extra={"document_id": document_id})
        if isinstance(exc, AppError):
            raise
        raise UpstreamUnavailableError("Storing the re-processed document failed. Reload it again.") from exc
    log.info("document reloaded", extra={"document_id": doc.id, "user_id": user_id, "chunks": len(chunks)})
    return doc, mode, previous


# ---------------------------------------------------------------- delete


async def delete_document(db: AsyncSession, c: Container, user_id: str, document_id: str) -> None:
    """Remove vectors first, confirm none remain, then delete the row (chunks and stored file cascade)."""
    doc = await get_document(db, user_id, document_id)
    remaining = await c.store.delete_document(user_id, document_id)
    if remaining:
        raise UpstreamUnavailableError("Vector store did not delete all chunks; nothing was removed.")
    await db.execute(delete(DocumentFile).where(DocumentFile.document_id == document_id))
    await db.delete(doc)
    await db.commit()
    log.info("document deleted", extra={"document_id": document_id, "user_id": user_id})


def upload_extensions() -> list[str]:
    """Accepted file extensions, for clients."""
    return sorted(EXTENSIONS)
