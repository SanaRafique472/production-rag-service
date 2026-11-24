"""Upload, list, get, read, reload and delete documents (always scoped to the current user)."""

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, get_container, get_db, rate_limit
from app.db.models import User
from app.errors import FileTooLargeError
from app.schemas import DocumentContent, DocumentList, DocumentOut, ReloadOut, UploadLimits, error_responses
from app.services import documents_service as svc
from app.services.container import Container

router = APIRouter(prefix="/documents", tags=["documents"])
_ERRORS = error_responses(
    "EMPTY_FILE",
    "NO_EXTRACTABLE_TEXT",
    "CORRUPT_FILE",
    "ENCRYPTED_FILE",
    "NOT_AUTHENTICATED",
    "DUPLICATE_DOCUMENT",
    "FILE_TOO_LARGE",
    "UNSUPPORTED_FILE_TYPE",
    "VALIDATION_ERROR",
    "RATE_LIMITED",
    "UPSTREAM_UNAVAILABLE",
)


@router.post(
    "",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("upload"))],
    responses=_ERRORS,
)
async def upload(
    file: UploadFile = File(..., description="PDF, DOCX, DOC, RTF, PPTX, TXT or MD"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    c: Container = Depends(get_container),
) -> DocumentOut:
    """Upload and index one file. Duplicate content for the same user returns 409."""
    limit = c.settings.max_upload_bytes
    data = await file.read(limit + 1)  # never read more than the limit allows
    if len(data) > limit:
        raise FileTooLargeError(
            f"File is larger than {c.settings.max_upload_mb} MB.", {"max_mb": c.settings.max_upload_mb}
        )
    doc = await svc.upload_document(db, c, user.id, file.filename or "upload", data)
    return (await svc.to_out(db, [doc]))[0]


@router.get("/limits", response_model=UploadLimits)
async def limits(c: Container = Depends(get_container)) -> UploadLimits:
    """Accepted extensions and size/page limits, so clients can validate before uploading."""
    return UploadLimits(
        max_upload_mb=c.settings.max_upload_mb, max_pages=c.settings.max_pages, extensions=svc.upload_extensions()
    )


@router.get("", response_model=DocumentList, responses=error_responses("NOT_AUTHENTICATED"))
async def list_documents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentList:
    """The current user's documents, newest first."""
    items, total = await svc.list_documents(db, user.id, limit, offset)
    return DocumentList(items=await svc.to_out(db, items), total=total)


@router.get(
    "/{document_id}",
    response_model=DocumentOut,
    responses=error_responses("NOT_AUTHENTICATED", "NOT_FOUND"),
)
async def get_document(
    document_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> DocumentOut:
    """One document (404 if it does not exist or belongs to someone else)."""
    doc = await svc.get_document(db, user.id, document_id)
    return (await svc.to_out(db, [doc]))[0]


@router.get(
    "/{document_id}/content",
    response_model=DocumentContent,
    responses=error_responses("NOT_AUTHENTICATED", "NOT_FOUND"),
)
async def get_content(
    document_id: str,
    offset: int = Query(0, ge=0, description="First block (chunk position) to return"),
    limit: int = Query(100, ge=1, le=1000),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentContent:
    """The extracted text exactly as it is used to answer questions, in reading order."""
    doc, blocks, total, tokens = await svc.get_content(db, user.id, document_id, offset, limit)
    return DocumentContent(
        document=(await svc.to_out(db, [doc]))[0], blocks=blocks, total_blocks=total, total_tokens=tokens
    )


@router.post(
    "/{document_id}/reload",
    response_model=ReloadOut,
    dependencies=[Depends(rate_limit("upload"))],
    responses=error_responses(
        "NOT_AUTHENTICATED",
        "NOT_FOUND",
        "NO_EXTRACTABLE_TEXT",
        "CORRUPT_FILE",
        "ENCRYPTED_FILE",
        "RATE_LIMITED",
        "UPSTREAM_UNAVAILABLE",
    ),
)
async def reload_document(
    document_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    c: Container = Depends(get_container),
) -> ReloadOut:
    """Re-process a document without uploading it again (re-extract the stored file, or re-index its text)."""
    doc, mode, previous = await svc.reload_document(db, c, user.id, document_id)
    return ReloadOut(document=(await svc.to_out(db, [doc]))[0], mode=mode, previous_chunk_count=previous)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses("NOT_AUTHENTICATED", "NOT_FOUND", "UPSTREAM_UNAVAILABLE"),
)
async def delete_document(
    document_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    c: Container = Depends(get_container),
) -> Response:
    """Delete the document, its chunk rows, its stored file and all of its vectors."""
    await svc.delete_document(db, c, user.id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
