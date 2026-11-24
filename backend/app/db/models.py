"""SQLAlchemy models (PostgreSQL in production, SQLite in tests).

PostgreSQL is the source of truth for users, documents, chunk text/metadata,
chats and request metrics. A chunk's primary key is also its Qdrant point id.
Deleting a document cascades to its chunks; vectors are removed by the service
before the row is deleted.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all tables."""


class User(Base):
    """An account; passwords are stored only as Argon2 hashes."""

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    documents: Mapped[list["Document"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Document(Base):
    """An uploaded file. ``UNIQUE(user_id, sha256)`` prevents duplicate uploads per user."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("user_id", "sha256", name="uq_document_user_hash"),
        Index("ix_documents_user_created", "user_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(10))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    empty_pages: Mapped[list] = mapped_column(JSON, default=list)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    flagged_chunks: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="processing")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    owner: Mapped[User] = relationship(back_populates="documents")
    chunks: Mapped[list["ChunkRow"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentFile(Base):
    """The original uploaded bytes, so a document can be re-processed without uploading it again.

    A separate table (not a column on ``documents``) so existing databases gain it
    through ``create_all`` without a migration, and listing documents never loads blobs.
    """

    __tablename__ = "document_files"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # message of the last failed processing
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChunkRow(Base):
    """Chunk text and citation metadata (vectors live in Qdrant under the same id)."""

    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_document_position", "document_id", "position"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    position: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10), default="text")
    chunking_config: Mapped[str] = mapped_column(String(10))
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False)
    document: Mapped[Document] = relationship(back_populates="chunks")


class ChatSession(Base):
    """A conversation thread."""

    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


class ChatMessage(Base):
    """One question or answer. Answers keep a snapshot of the chunks they used."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_messages_session_created", "session_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(10))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)
    refusal_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    retrieved_chunks: Mapped[list] = mapped_column(JSON, default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    session: Mapped[ChatSession] = relationship(back_populates="messages")


class RequestMetric(Base):
    """One row per API request: the data behind /metrics and the monitoring page."""

    __tablename__ = "request_metrics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    method: Mapped[str] = mapped_column(String(10))
    route: Mapped[str] = mapped_column(String(100), index=True)
    status_code: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    embed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    search_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rerank_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    refused: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
