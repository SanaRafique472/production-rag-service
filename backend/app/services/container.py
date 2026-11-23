"""Build every provider client once at startup.

Real providers are used when their keys are configured. A missing key gives a
``MissingKeyProvider`` that answers 503 with a clear message, so the service
never silently falls back to fake answers. Fakes are used only when
``USE_FAKE_PROVIDERS=true`` (tests).
"""

from dataclasses import dataclass

from app.clients.embeddings import CohereEmbedder, Embedder, HashingEmbedder
from app.clients.llm import LLM, ClaudeLLM, CohereLLM, FakeLLM
from app.clients.qdrant_store import QdrantStore
from app.clients.reranker import CohereReranker, OverlapReranker, Reranker
from app.clients.resilience import MissingKeyProvider
from app.config import Settings
from app.generation import Generator
from app.guardrails import make_canary
from app.retrieval import RetrievalConfig, Retriever


@dataclass
class Container:
    """All long-lived clients and pipeline objects."""

    settings: Settings
    embedder: Embedder
    reranker: Reranker
    llm: LLM
    store: QdrantStore
    retriever: Retriever
    generator: Generator
    mode: str  # "live" or "fake"


def build_container(settings: Settings) -> Container:
    """Create clients according to configuration."""
    if settings.use_fake_providers:
        embedder: Embedder = HashingEmbedder(256)
        reranker: Reranker = OverlapReranker()
        llm: LLM = FakeLLM()
        dim, mode = 256, "fake"
    else:
        embedder = (
            CohereEmbedder(settings) if settings.cohere_api_key else MissingKeyProvider("Cohere", "COHERE_API_KEY")
        )  # type: ignore[assignment]
        reranker = (
            CohereReranker(settings) if settings.cohere_api_key else MissingKeyProvider("Cohere", "COHERE_API_KEY")
        )  # type: ignore[assignment]
        if settings.llm_provider == "cohere":
            llm = CohereLLM(settings) if settings.cohere_api_key else MissingKeyProvider("Cohere", "COHERE_API_KEY")  # type: ignore[assignment]
        else:
            llm = (
                ClaudeLLM(settings) if settings.anthropic_api_key else MissingKeyProvider("Claude", "ANTHROPIC_API_KEY")
            )  # type: ignore[assignment]
        dim, mode = settings.embed_dim, "live"
    store = QdrantStore(settings, dim=dim)
    config = RetrievalConfig.from_settings(settings)
    if settings.use_fake_providers:  # fake scores live on a different scale
        config = RetrievalConfig(**{**config.__dict__, "rerank_threshold": 0.2, "dense_threshold": 0.1})
    retriever = Retriever(embedder, store, reranker, config)
    return Container(settings, embedder, reranker, llm, store, retriever, Generator(llm, make_canary()), mode)
