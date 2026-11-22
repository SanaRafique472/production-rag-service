"""Document use cases: upload (ingest), list, get and delete.

Upload order matters for cost and safety:
1. cheap checks first (type, size, magic bytes) -> 400/413/415;
2. duplicate check by SHA-256 before any paid embedding call -> 409;
3. CPU-heavy extraction in a worker thread with a timeout;
4. embeddings (parallel batches), then DB rows and Qdrant points;
5. if storing vectors fails, the DB transaction is rolled back and any partial
   points are removed, so a document is never half-stored.
"""

import asyncio
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.sparse import doc_sparse
from app.db.models import ChunkRow, Document
from app.errors import CorruptFileError, DuplicateDocumentError, NotFoundError, UpstreamUnavailableError
from app.guardrails import scan_document_text
from app.ingestion.pipeline import process_file
from app.ingestion.validation import detect_file_type, safe_filename, sha256_hex
from app.services.container import Container

log = logging.getLogger(__name__)


async def upload_document(db: AsyncSession, c: Container, user_id: str, filename: str, data: bytes) -> Document:
    """Validate, ingest, embed and store one file for ``user_id``."""
    s = c.settings
    name = safe_filename(filename)
    detect_file_type(name, data, s)
    digest = sha256_hex(data)
    existing = await db.scalar(select(Document).where(Document.user_id == user_id, Document.sha256 == digest))
    if existing is not None:
        raise DuplicateDocumentError(
            f"This file was already uploaded as {existing.filename}.",
            {"existing_document_id": existing.id, "filename": existing.filename},
        )

    doc_id = str(uuid.uuid4())
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(process_file, name, data, document_id=doc_id, settings=s), s.ingestion_timeout_s
        )
    except TimeoutError as exc:
        raise CorruptFileError("The document took too long to process and was rejected.") from exc

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
    db.add_all(
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
    )
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
    log.info("document ingested", extra={"document_id": doc_id, "user_id": user_id})
    return doc


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


async def delete_document(db: AsyncSession, c: Container, user_id: str, document_id: str) -> None:
    """Remove vectors first, confirm none remain, then delete the row (chunks cascade)."""
    doc = await get_document(db, user_id, document_id)
    remaining = await c.store.delete_document(user_id, document_id)
    if remaining:
        raise UpstreamUnavailableError("Vector store did not delete all chunks; nothing was removed.")
    await db.delete(doc)
    await db.commit()
    log.info("document deleted", extra={"document_id": document_id, "user_id": user_id})
