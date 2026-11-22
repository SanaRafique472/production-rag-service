"""Rerankers.

A reranker (cross-encoder) reads the question and each candidate chunk together
and returns a relevance score. Cohere Rerank 5 scores are calibrated to 0-1, so
a fixed threshold can decide whether anything relevant was found.
"""

from typing import Protocol

import cohere

from app.clients.resilience import call_external
from app.clients.sparse import tokenize
from app.config import Settings


class Reranker(Protocol):
    """Interface: return (candidate index, score) pairs, best first."""

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """Score ``documents`` against ``query``."""
        ...


class CohereReranker:
    """Cohere Rerank 5 via the async v2 SDK."""

    def __init__(self, settings: Settings) -> None:
        """Create the SDK client."""
        assert settings.cohere_api_key is not None
        self._s = settings
        self._client = cohere.AsyncClientV2(
            api_key=settings.cohere_api_key.get_secret_value(),
            timeout=settings.http_timeout_s,
            max_retries=0,
        )

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """Rerank all documents; returns every index with its calibrated score."""
        if not documents:
            return []

        async def call() -> list[tuple[int, float]]:
            resp = await self._client.rerank(
                model=self._s.rerank_model, query=query, documents=documents, top_n=len(documents)
            )
            return [(r.index, float(r.relevance_score)) for r in resp.results]

        return await call_external(
            "Cohere rerank", call, attempts=self._s.max_retries, timeout_s=self._s.http_timeout_s
        )


class OverlapReranker:
    """Offline stand-in: share of query words found in the chunk (tests only)."""

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """Score by word overlap, scaled to 0-1."""
        q = set(tokenize(query))
        scores = [(i, len(q & set(tokenize(d))) / len(q) if q else 0.0) for i, d in enumerate(documents)]
        return sorted(scores, key=lambda s: s[1], reverse=True)
