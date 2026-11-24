"""Central settings for the service.

Every tunable value lives here. Secrets are read from environment variables
(or a local ``.env`` file) and are typed as ``SecretStr`` so they are never
printed in logs or error messages by accident.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from the environment."""

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # --- App ---
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:5173"]

    # --- Auth ---
    jwt_secret_key: SecretStr = SecretStr("change-me")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # --- Storage ---
    database_url: str = "sqlite+aiosqlite:///./rag.sqlite3"
    qdrant_url: str | None = None  # None -> in-process Qdrant (tests / notebooks)
    qdrant_path: str | None = None  # on-disk local Qdrant when no server is available
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = "chunks"

    # --- Providers (empty -> feature disabled, never hard-coded) ---
    anthropic_api_key: SecretStr | None = None
    cohere_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    # --- Upload limits ---
    max_upload_mb: int = Field(50, description="Hard limit on upload size in megabytes")
    max_pages: int = Field(1000, description="Maximum pages or slides per document")
    max_zip_uncompressed_mb: int = 400  # zip-bomb guard for DOCX/PPTX: total expanded size
    max_zip_members: int = 20000  # zip-bomb guard: number of parts in the container
    max_zip_ratio: int = 200  # compression ratio treated as suspicious ...
    zip_ratio_min_member_mb: int = 50  # ... but only for members that expand beyond this size
    min_chars_per_page: int = 20  # below this on average -> "no extractable text"
    # Legacy .doc / .rtf: "auto" uses LibreOffice when installed (best fidelity), else the built-in reader.
    doc_converter: Literal["auto", "libreoffice", "builtin"] = "auto"
    libreoffice_timeout_s: float = 90.0
    store_original_files: bool = True  # keep uploads so a document can be re-processed without re-uploading

    # --- Chunking (proposed configuration B) ---
    chunk_size_tokens: int = 400
    chunk_overlap_tokens: int = 60

    # --- Questions ---
    max_question_chars: int = 1000

    # --- Providers / models ---
    use_fake_providers: bool = Field(
        False, description="Offline deterministic fakes for tests. Never use for real answers."
    )
    embed_model_index: str = "embed-v5.0-pro"
    embed_model_query: str = "embed-v5.0-fast"
    embed_dim: int = 1024
    embed_batch_size: int = 96
    embed_concurrency: int = 4
    rerank_model: str = "rerank-v5.0-fast"
    # Which LLM writes the answers: "cohere" (Command A+, free on a trial key) or "anthropic" (Claude).
    llm_provider: Literal["cohere", "anthropic"] = "cohere"
    llm_model: str = "claude-haiku-5-5"  # used when llm_provider = "anthropic"
    cohere_llm_model: str = "command-a-plus-05-2026"  # used when llm_provider = "cohere"
    # Evaluation judge: "cohere" (free, same key) or "openai" (needs OPENAI_API_KEY).
    judge_provider: Literal["cohere", "openai"] = "cohere"
    cohere_judge_model: str = "command-a-03-2025"  # a different model from the answer model
    llm_max_tokens: int = 1000
    judge_model: str = "gpt-6-luna"  # OpenAI judge model, used when judge_provider = "openai"
    use_llm_classifier: bool = True  # extra guardrail call, run in parallel with retrieval

    # --- Retrieval (configuration B defaults; evals override per config) ---
    retrieval_hybrid: bool = True
    retrieval_dense_k: int = 40
    retrieval_sparse_k: int = 40
    retrieval_fused_k: int = 30
    retrieval_rerank: bool = True
    retrieval_top_n: int = 6
    rerank_threshold: float = 0.35  # calibrated 0-1 score; tuned on the golden set
    dense_threshold: float = 0.30  # cosine floor when reranking is off; tuned on the golden set
    # When the question is scoped to documents this small (in tokens), the whole document is sent to
    # the model instead of only the top chunks, so summaries and "whole document" questions work.
    full_context_max_tokens: int = 12000

    # --- Timeouts / retries (every external call) ---
    http_timeout_s: float = 30.0
    max_retries: int = 3
    ingestion_timeout_s: float = 240.0

    # --- Rate limits (per user or IP) ---
    rate_limit_ask: str = "20/minute"
    rate_limit_upload: str = "10/minute"
    rate_limit_auth: str = "10/minute"

    # --- Cost table, USD per million tokens (checked Nov 2026; update if prices change) ---
    price_llm_in: float = 0.10
    price_llm_out: float = 0.50
    price_embed_query: float = 0.08
    price_rerank: float = 0.02

    @field_validator(
        "anthropic_api_key",
        "cohere_api_key",
        "openai_api_key",
        "qdrant_api_key",
        "qdrant_url",
        "qdrant_path",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, v):  # noqa: ANN001, ANN206
        """Treat ``KEY=`` in .env as "not set"."""
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("database_url", mode="before")
    @classmethod
    def _default_db(cls, v):  # noqa: ANN001, ANN206
        return "sqlite+aiosqlite:///./rag.sqlite3" if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _free_llm_costs_nothing(self) -> "Settings":
        """Command A+ is free until rate limits, so its token price is 0 unless set explicitly."""
        if self.llm_provider == "cohere":
            if "price_llm_in" not in self.model_fields_set:
                self.price_llm_in = 0.0
            if "price_llm_out" not in self.model_fields_set:
                self.price_llm_out = 0.0
        return self

    @model_validator(mode="after")
    def _safe_in_production(self) -> "Settings":
        """Refuse to start in production with the placeholder JWT secret or fake providers."""
        if self.app_env == "production":
            if self.jwt_secret_key.get_secret_value() in ("", "change-me") or len(self.jwt_secret_key) < 32:
                raise ValueError("Set JWT_SECRET_KEY to a random value of at least 32 characters in production.")
            if self.use_fake_providers:
                raise ValueError("USE_FAKE_PROVIDERS must be false in production.")
        return self

    @property
    def max_upload_bytes(self) -> int:
        """Upload size limit in bytes."""
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance (one per process)."""
    return Settings()
