"""Retrieval: query embedding, hybrid search, fusion, reranking and the score gate.

How a question finds its chunks:
1. Embed the question (dense) and build its BM25 keyword vector (sparse).
2. Run both searches **in parallel**, always filtered to the user's own chunks.
3. Merge the two ranked lists with Reciprocal Rank Fusion:
   ``rrf(chunk) = sum over lists of 1 / (60 + rank)``. Rank-based fusion means
   the two very different score scales never have to be balanced by hand.
4. Optionally rerank the top candidates with a cross-encoder.
5. Keep at most ``top_n`` chunks whose score passes the threshold. If none
   pass, the caller refuses without calling the LLM.

Every candidate keeps its cosine similarity so the UI can show it.
"""

import asyncio
import math
import time
from dataclasses import dataclass, field

from app.clients.embeddings import Embedder
from app.clients.qdrant_store import Hit, QdrantStore
from app.clients.reranker import Reranker
from app.clients.sparse import query_sparse
from app.config import Settings
from app.ingestion.chunking import count_tokens

RRF_K = 60


@dataclass(frozen=True)
class RetrievalConfig:
    """Retrieval settings; evaluation configurations override these."""

    name: str = "B"
    hybrid: bool = True
    dense_k: int = 40
    sparse_k: int = 40
    fused_k: int = 30
    rerank: bool = True
    top_n: int = 6
    rerank_threshold: float = 0.35
    dense_threshold: float = 0.30

    @classmethod
    def from_settings(cls, s: Settings) -> "RetrievalConfig":
        """Build the service's default configuration from settings."""
        return cls(
            hybrid=s.retrieval_hybrid,
            dense_k=s.retrieval_dense_k,
            sparse_k=s.retrieval_sparse_k,
            fused_k=s.retrieval_fused_k,
            rerank=s.retrieval_rerank,
            top_n=s.retrieval_top_n,
            rerank_threshold=s.rerank_threshold,
            dense_threshold=s.dense_threshold,
        )


@dataclass
class RetrievedChunk:
    """A candidate chunk with every score the pipeline computed."""

    chunk_id: str
    document_id: str
    filename: str
    position: int
    page_start: int | None
    page_end: int | None
    section: str | None
    text: str
    kind: str
    suspicious: bool
    similarity: float | None = None  # cosine similarity with the question
    dense_rank: int | None = None
    bm25_rank: int | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None
    kept: bool = False

    @property
    def title(self) -> str:
        """Short citation label such as 'policy.pdf, p. 4' or 'notes.docx, Leave > Sick'."""
        if self.page_start:
            pages = (
                f"p. {self.page_start}"
                if self.page_start == self.page_end
                else f"pp. {self.page_start}-{self.page_end}"
            )
            return f"{self.filename}, {pages}"
        return f"{self.filename}, {self.section}" if self.section else self.filename


@dataclass
class RetrievalResult:
    """All candidates (for the evidence panel) and the chunks that passed the gate."""

    candidates: list[RetrievedChunk]
    kept: list[RetrievedChunk]
    timings_ms: dict[str, int] = field(default_factory=dict)
    rerank_tokens: int = 0
    query_tokens: int = 0


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity: dot product divided by both lengths (1 = same direction)."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Fuse ranked id lists: each list adds 1/(k + rank) for every id it contains."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def _to_chunk(hit: Hit) -> RetrievedChunk:
    p = hit.payload
    return RetrievedChunk(
        chunk_id=hit.chunk_id,
        document_id=p.get("document_id", ""),
        filename=p.get("filename", ""),
        position=int(p.get("position", 0)),
        page_start=p.get("page_start"),
        page_end=p.get("page_end"),
        section=p.get("section"),
        text=p.get("text", ""),
        kind=p.get("kind", "text"),
        suspicious=bool(p.get("suspicious", False)),
    )


class Retriever:
    """Runs the retrieval steps for one configuration."""

    def __init__(
        self, embedder: Embedder, store: QdrantStore, reranker: Reranker | None, config: RetrievalConfig
    ) -> None:
        """Wire the clients used by every retrieval."""
        self.embedder, self.store, self.reranker, self.config = embedder, store, reranker, config

    async def retrieve(self, question: str, user_id: str, document_ids: list[str] | None = None) -> RetrievalResult:
        """Return candidates and kept chunks for ``question`` within the user's documents."""
        cfg, timings = self.config, {}
        t0 = time.perf_counter()
        qvec = await self.embedder.embed_query(question)
        timings["embed_ms"] = int((time.perf_counter() - t0) * 1000)

        t1 = time.perf_counter()
        dense_task = self.store.search_dense(qvec, user_id, cfg.dense_k, document_ids)
        if cfg.hybrid:
            dense_hits, sparse_hits = await asyncio.gather(
                dense_task, self.store.search_sparse(query_sparse(question), user_id, cfg.sparse_k, document_ids)
            )
        else:
            dense_hits, sparse_hits = await dense_task, []
        timings["search_ms"] = int((time.perf_counter() - t1) * 1000)

        by_id: dict[str, RetrievedChunk] = {}
        for rank, hit in enumerate(dense_hits, start=1):
            c = by_id.setdefault(hit.chunk_id, _to_chunk(hit))
            c.dense_rank, c.similarity = rank, hit.score
        for rank, hit in enumerate(sparse_hits, start=1):
            c = by_id.setdefault(hit.chunk_id, _to_chunk(hit))
            c.bm25_rank = rank
            if c.similarity is None and hit.vector is not None:
                c.similarity = cosine(qvec, hit.vector)

        fused = reciprocal_rank_fusion([[h.chunk_id for h in dense_hits], [h.chunk_id for h in sparse_hits]])
        for chunk_id, score in fused.items():
            by_id[chunk_id].rrf_score = score
        candidates = sorted(by_id.values(), key=lambda c: c.rrf_score, reverse=True)[: cfg.fused_k]

        rerank_tokens = 0
        if cfg.rerank and self.reranker is not None and candidates:
            t2 = time.perf_counter()
            scores = await self.reranker.rerank(question, [c.text for c in candidates])
            timings["rerank_ms"] = int((time.perf_counter() - t2) * 1000)
            rerank_tokens = sum(count_tokens(c.text) for c in candidates) + count_tokens(question) * len(candidates)
            for idx, score in scores:
                candidates[idx].rerank_score = score
            candidates.sort(key=lambda c: c.rerank_score or 0.0, reverse=True)
            kept = [c for c in candidates if (c.rerank_score or 0.0) >= cfg.rerank_threshold][: cfg.top_n]
        else:
            kept = [c for c in candidates if (c.similarity or 0.0) >= cfg.dense_threshold][: cfg.top_n]
        for c in kept:
            c.kept = True
        timings["retrieval_ms"] = int((time.perf_counter() - t0) * 1000)
        return RetrievalResult(candidates, kept, timings, rerank_tokens, count_tokens(question))
