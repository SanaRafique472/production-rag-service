"""Ask questions and read chat history."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, get_container, get_db, rate_limit
from app.db.models import User
from app.observability.logging import request_id_var
from app.schemas import AskRequest, AskResponse, HistoryOut, SessionOut, error_responses
from app.services import chat_service
from app.services.container import Container

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/ask",
    response_model=AskResponse,
    dependencies=[Depends(rate_limit("ask"))],
    responses=error_responses(
        "PROMPT_INJECTION_DETECTED",
        "JAILBREAK_DETECTED",
        "NOT_AUTHENTICATED",
        "NOT_FOUND",
        "VALIDATION_ERROR",
        "INVALID_QUESTION",
        "RATE_LIMITED",
        "UPSTREAM_RATE_LIMITED",
        "UPSTREAM_UNAVAILABLE",
    ),
)
async def ask(
    body: AskRequest,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    c: Container = Depends(get_container),
) -> dict:
    """Answer from the user's documents with citations, or refuse when the answer is not there."""
    response, metric = await chat_service.ask(db, c, user.id, body, request_id_var.get())
    request.state.metric = metric  # picked up by the metrics middleware
    return response


@router.get("/history", response_model=HistoryOut, responses=error_responses("NOT_AUTHENTICATED", "NOT_FOUND"))
async def history(
    session_id: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> HistoryOut:
    """Chat sessions with their messages (only the current user's)."""
    sessions = await chat_service.history(db, user.id, session_id, limit)
    return HistoryOut(sessions=[SessionOut.model_validate(s) for s in sessions])
