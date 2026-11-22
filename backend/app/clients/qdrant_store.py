"""Qdrant vector store.

One collection holds every user's chunks. Each point has:
* a named dense vector ``dense`` (cosine similarity),
* a named sparse vector ``bm25`` (Qdrant applies IDF),
* a payload with the citation metadata and ``user_id`` / ``document_id``.

Every search and delete is filtered by ``user_id``: a user can never reach
another user's chunks, even if a document id is guessed.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.clients.resilience import call_external
from app.config import Settings
from app.ingestion.chunking import Chunk


@dataclass
class Hit:
    """One search result from Qdrant."""

    chunk_id: str
    score: float
    payload: dict[str, Any]
    vector: list[float] | None = None  # dense vector, returned by keyword search for cosine scoring


class QdrantStore:
    """Thin async wrapper around Qdrant with retries and tenant filtering."""

    def __init__(self, settings: Settings, collection: str | None = None, dim: int | None = None) -> None:
        """Connect to a server (``qdrant_url``), a local path, or in-memory Qdrant."""
        self._s = settings
        self.collection = collection or settings.qdrant_collection
        self.dim = dim or settings.embed_dim
        key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
        if settings.qdrant_url:
            self._client = AsyncQdrantClient(url=settings.qdrant_url, api_key=key, timeout=int(settings.http_timeout_s))
        elif settings.qdrant_path:
            self._client = AsyncQdrantClient(path=settings.qdrant_path)
        else:
            self._client = AsyncQdrantClient(location=":memory:")
        self._lock = asyncio.Lock()

    async def _call(self, fn):  # noqa: ANN001, ANN202
        return await call_external("Qdrant", fn, attempts=self._s.max_retries, timeout_s=self._s.http_timeout_s)

    async def ensure_collection(self) -> None:
        """Create the collection and payload indexes if they do not exist."""
        async with self._lock:
            if await self._call(lambda: self._client.collection_exists(self.collection)):
                return
            await self._call(
                lambda: self._client.create_collection(
                    self.collection,
                    vectors_config={"dense": models.VectorParams(size=self.dim, distance=models.Distance.COSINE)},
                    sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
                )
            )
            if self._s.qdrant_url:  # payload indexes only matter on a real server
                await self._call(
                    lambda: self._client.create_payload_index(
                        self.collection, "user_id", models.KeywordIndexParams(type="keyword", is_tenant=True)
                    )
                )
                await self._call(lambda: self._client.create_payload_index(self.collection, "document_id", "keyword"))

    async def upsert(
        self,
        chunks: list[Chunk],
        dense: list[list[float]],
        sparse: list[tuple[list[int], list[float]]],
        user_id: str,
        flags: list[bool] | None = None,
    ) -> None:
        """Store chunks with both vectors and their metadata payload."""
        points = [
            models.PointStruct(
                id=c.id,
                vector={"dense": d, "bm25": models.SparseVector(indices=s[0], values=s[1])},
                payload={
                    "user_id": user_id,
                    "document_id": c.document_id,
                    "filename": c.filename,
                    "position": c.position,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "section": c.section,
                    "text": c.text,
                    "kind": c.kind,
                    "chunking_config": c.chunking_config,
                    "suspicious": bool(flags[i]) if flags else False,
                },
            )
            for i, (c, d, s) in enumerate(zip(chunks, dense, sparse, strict=True))
        ]
        for i in range(0, len(points), 256):
            batch = points[i : i + 256]
            await self._call(lambda b=batch: self._client.upsert(self.collection, b, wait=True))

    @staticmethod
    def _filter(user_id: str, document_ids: list[str] | None = None) -> models.Filter:
        must: list[models.Condition] = [models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))]
        if document_ids:
            must.append(models.FieldCondition(key="document_id", match=models.MatchAny(any=document_ids)))
        return models.Filter(must=must)

    async def search_dense(
        self, vector: list[float], user_id: str, limit: int, document_ids: list[str] | None = None
    ) -> list[Hit]:
        """Cosine-similarity search restricted to one user's chunks."""
        res = await self._call(
            lambda: self._client.query_points(
                self.collection,
                query=vector,
                using="dense",
                limit=limit,
                query_filter=self._filter(user_id, document_ids),
                with_payload=True,
            )
        )
        return [Hit(str(p.id), float(p.score), p.payload or {}) for p in res.points]

    async def search_sparse(
        self, sparse: tuple[list[int], list[float]], user_id: str, limit: int, document_ids: list[str] | None = None
    ) -> list[Hit]:
        """BM25 keyword search restricted to one user's chunks."""
        if not sparse[0]:
            return []
        res = await self._call(
            lambda: self._client.query_points(
                self.collection,
                query=models.SparseVector(indices=sparse[0], values=sparse[1]),
                using="bm25",
                limit=limit,
                query_filter=self._filter(user_id, document_ids),
                with_payload=True,
                with_vectors=["dense"],
            )
        )
        return [
            Hit(str(p.id), float(p.score), p.payload or {}, (p.vector or {}).get("dense"))  # type: ignore[union-attr]
            for p in res.points
        ]

    async def count_document(self, user_id: str, document_id: str) -> int:
        """Number of stored chunks for one document of one user."""
        res = await self._call(
            lambda: self._client.count(self.collection, count_filter=self._filter(user_id, [document_id]), exact=True)
        )
        return int(res.count)

    async def delete_document(self, user_id: str, document_id: str) -> int:
        """Delete every point of a document and return how many remain (should be 0)."""
        await self._call(
            lambda: self._client.delete(
                self.collection,
                points_selector=models.FilterSelector(filter=self._filter(user_id, [document_id])),
                wait=True,
            )
        )
        return await self.count_document(user_id, document_id)

    async def ping(self) -> bool:
        """Return True when Qdrant answers."""
        try:
            await asyncio.wait_for(self._client.get_collections(), timeout=5)
            return True
        except Exception:
            return False
