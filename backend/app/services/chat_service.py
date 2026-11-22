"""The ask use case: guardrails, retrieval, generation, persistence and metrics.

Flow for one question:
1. Pattern guardrails (instant) -> 400 for injection/jailbreak, 422 for empty.
2. No documents yet -> polite refusal (no paid calls).
3. Retrieval and the LLM classifier run **in parallel** (``asyncio.gather``).
4. Classifier says injection/jailbreak -> 400. Nothing passes the score gate ->
   refusal (reason ``off_topic`` or ``no_relevant_chunks``), no LLM call.
5. Generation with validated citations; output guardrail checks for leaks.
6. Question and answer (with a snapshot of the evidence) are saved.
"""

import asyncio
import logging
import time
from dataclasses import asdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, ChatSession, Document
from app.errors import InvalidQuestionError, JailbreakError, NotFoundError, PromptInjectionError
from app.generation import GenerationResult, detect_language, refusal_message
from app.guardrails import check_output, check_question
from app.retrieval import RetrievalResult
from app.schemas import AskRequest
from app.services.container import Container

log = logging.getLogger(__name__)


async def _classify(c: Container, question: str) -> tuple[str, int, int]:
    """Run the LLM classifier; if it fails, fall back to 'ok' (pattern checks already ran)."""
    if not c.settings.use_llm_classifier:
        return "ok", 0, 0
    try:
        return await c.llm.classify(question)
    except Exception:
        log.warning("classifier unavailable; continuing with pattern guardrails only")
        return "ok", 0, 0


async def _session(db: AsyncSession, user_id: str, session_id: str | None, title: str) -> ChatSession:
    if session_id:
        session = await db.scalar(
            select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
        )
        if session is None:
            raise NotFoundError("Chat session not found.")
        return session
    session = ChatSession(user_id=user_id, title=title[:80])
    db.add(session)
    await db.flush()
    return session


async def ask(db: AsyncSession, c: Container, user_id: str, req: AskRequest, request_id: str) -> tuple[dict, dict]:
    """Answer one question. Returns (response dict, metric fields for monitoring)."""
    s, t0 = c.settings, time.perf_counter()
    question = check_question(req.question)
    if len(question) > s.max_question_chars:
        raise InvalidQuestionError(f"Questions are limited to {s.max_question_chars} characters.")
    language = detect_language(question)

    doc_filter = [Document.user_id == user_id, Document.status == "ready"]
    if req.document_ids:
        owned = await db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.user_id == user_id, Document.id.in_(req.document_ids))
        )
        if owned != len(set(req.document_ids)):
            raise NotFoundError("One or more selected documents were not found.")
        doc_filter.append(Document.id.in_(req.document_ids))
    doc_count = await db.scalar(select(func.count()).select_from(Document).where(*doc_filter)) or 0

    retrieval: RetrievalResult | None = None
    gen = GenerationResult(answer="")
    cls_in = cls_out = 0
    llm_ms: int | None = None
    if doc_count == 0:
        gen = GenerationResult(
            answer=refusal_message("no_documents_uploaded", language),
            refused=True,
            refusal_reason="no_documents_uploaded",
        )
    else:
        retrieval, (label, cls_in, cls_out) = await asyncio.gather(
            c.retriever.retrieve(question, user_id, req.document_ids), _classify(c, question)
        )
        if label == "injection":
            raise PromptInjectionError(
                "This request tries to change or reveal the assistant's instructions and was blocked."
            )
        if label == "jailbreak":
            raise JailbreakError("This request tries to bypass the assistant's rules and was blocked.")
        if label == "off_topic" or not retrieval.kept:  # e.g. "write a poem": not a document question
            reason = "off_topic" if label == "off_topic" else "no_relevant_chunks"
            gen = GenerationResult(answer=refusal_message(reason, language), refused=True, refusal_reason=reason)
        else:
            t_llm = time.perf_counter()
            gen = await c.generator.generate(question, retrieval.kept, language)
            llm_ms = int((time.perf_counter() - t_llm) * 1000)
            leak = check_output(gen.answer, c.generator.canary, c.generator.system_prompt)
            if leak:
                log.warning("output guardrail blocked an answer", extra={"error_type": leak})
                gen = GenerationResult(
                    answer=refusal_message("output_blocked", language),
                    refused=True,
                    refusal_reason="output_blocked",
                    tokens_in=gen.tokens_in,
                    tokens_out=gen.tokens_out,
                )

    tokens_in, tokens_out = gen.tokens_in + cls_in, gen.tokens_out + cls_out
    rerank_tokens = retrieval.rerank_tokens if retrieval else 0
    query_tokens = retrieval.query_tokens if retrieval else 0
    cost = (
        tokens_in * s.price_llm_in
        + tokens_out * s.price_llm_out
        + rerank_tokens * s.price_rerank
        + query_tokens * s.price_embed_query
    ) / 1_000_000

    cited_ids = {ct.chunk_id for ct in gen.citations}
    chunks_out = [
        {**{k: v for k, v in asdict(ch).items()}, "cited": ch.chunk_id in cited_ids}
        for ch in (retrieval.candidates if retrieval else [])
    ]
    citations_out = [asdict(ct) for ct in gen.citations]
    latency_ms = int((time.perf_counter() - t0) * 1000)
    timings = {**(retrieval.timings_ms if retrieval else {}), **({"llm_ms": llm_ms} if llm_ms is not None else {})}

    session = await _session(db, user_id, req.session_id, question)
    db.add(ChatMessage(session_id=session.id, role="user", content=question))
    answer_msg = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=gen.answer,
        refused=gen.refused,
        refusal_reason=gen.refusal_reason,
        citations=citations_out,
        retrieved_chunks=[ch for ch in chunks_out if ch["kept"]],
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
    )
    db.add(answer_msg)
    await db.commit()

    response = {
        "answer": gen.answer,
        "refused": gen.refused,
        "refusal_reason": gen.refusal_reason,
        "language": language,
        "session_id": session.id,
        "message_id": answer_msg.id,
        "citations": citations_out,
        "retrieved_count": len(chunks_out),
        "kept_count": sum(1 for ch in chunks_out if ch["kept"]),
        "retrieved_chunks": chunks_out,
        "retrieval_config": c.retriever.config.name,
        "usage": {"tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": round(cost, 6)},
        "latency_ms": latency_ms,
        "timings_ms": timings,
        "request_id": request_id,
    }
    metric = {
        "embed_ms": timings.get("embed_ms"),
        "search_ms": timings.get("search_ms"),
        "rerank_ms": timings.get("rerank_ms"),
        "llm_ms": llm_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        "refused": gen.refused,
    }
    return response, metric


async def history(db: AsyncSession, user_id: str, session_id: str | None, limit: int) -> list[ChatSession]:
    """Return the user's sessions (newest first) with their messages."""
    query = select(ChatSession).where(ChatSession.user_id == user_id)
    if session_id:
        query = query.where(ChatSession.id == session_id)
    sessions = list(await db.scalars(query.order_by(ChatSession.created_at.desc()).limit(limit)))
    if session_id and not sessions:
        raise NotFoundError("Chat session not found.")
    for sess in sessions:
        await db.refresh(sess, ["messages"])
    return sessions
