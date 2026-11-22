"""Embedding clients.

``CohereEmbedder`` is the real one: documents are embedded with Embed 5 Pro and
questions with Embed 5 Fast (they share one vector space). Batches of 96 texts
are sent in parallel with a concurrency cap.

``HashingEmbedder`` is an offline, deterministic stand-in for tests: it hashes
words into a fixed-size vector, so texts sharing words get similar vectors.
It is never used to produce real answers or evaluation numbers.
"""

import asyncio
import hashlib
import math
import re
from typing import Protocol

import cohere

from app.clients.resilience import call_external
from app.config import Settings

_WORD = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    """Interface every embedding client implements."""

    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed chunk texts for indexing."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed one question for search."""
        ...


class CohereEmbedder:
    """Cohere Embed 5 via the async v2 SDK, with retries and batching."""

    def __init__(self, settings: Settings) -> None:
        """Create the SDK client; SDK retries are off because we retry ourselves."""
        assert settings.cohere_api_key is not None
        self._s = settings
        self.dim = settings.embed_dim
        self._client = cohere.AsyncClientV2(
            api_key=settings.cohere_api_key.get_secret_value(),
            timeout=settings.http_timeout_s,
            max_retries=0,
        )

    async def _embed(self, texts: list[str], model: str, input_type: str) -> list[list[float]]:
        async def call() -> list[list[float]]:
            resp = await self._client.embed(
                model=model,
                input_type=input_type,
                texts=texts,
                embedding_types=["float"],
                output_dimension=self.dim,
            )
            return [list(v) for v in resp.embeddings.float_ or []]

        return await call_external(
            "Cohere embeddings", call, attempts=self._s.max_retries, timeout_s=self._s.http_timeout_s
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts: batches of ``embed_batch_size`` sent concurrently."""
        size = self._s.embed_batch_size
        batches = [texts[i : i + size] for i in range(0, len(texts), size)]
        gate = asyncio.Semaphore(self._s.embed_concurrency)

        async def run(batch: list[str]) -> list[list[float]]:
            async with gate:
                return await self._embed(batch, self._s.embed_model_index, "search_document")

        results = await asyncio.gather(*(run(b) for b in batches))
        return [vec for batch in results for vec in batch]

    async def embed_query(self, text: str) -> list[float]:
        """Embed a question with the fast model."""
        return (await self._embed([text], self._s.embed_model_query, "search_query"))[0]


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedder (tests and offline demos only)."""

    def __init__(self, dim: int = 256) -> None:
        """Set the vector size."""
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in _WORD.findall(text.lower()):
            h = int(hashlib.md5(word.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 8) % 2 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed texts locally."""
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        """Embed one text locally."""
        return self._vec(text)
