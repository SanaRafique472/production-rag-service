"""Central settings for the service.

Every tunable value lives here. Secrets are read from environment variables
(or a local ``.env`` file) and are typed as ``SecretStr`` so they are never
printed in logs or error messages by accident.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
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
    max_upload_mb: int = Field(20, description="Hard limit on upload size in megabytes")
    max_pages: int = Field(300, description="Maximum pages or slides per document")
    max_zip_uncompressed_mb: int = 200  # zip-bomb guard for DOCX/PPTX
    max_zip_ratio: int = 100  # max compression ratio of any member
    min_chars_per_page: int = 20  # below this on average -> "no extractable text"

    # --- Chunking (proposed configuration B) ---
    chunk_size_tokens: int = 400
    chunk_overlap_tokens: int = 60

    # --- Questions ---
    max_question_chars: int = 1000

    @property
    def max_upload_bytes(self) -> int:
        """Upload size limit in bytes."""
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance (one per process)."""
    return Settings()
