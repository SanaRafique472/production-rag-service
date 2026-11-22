"""Pydantic request and response models (with OpenAPI examples)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ErrorBody(BaseModel):
    """Body of every error response."""

    code: str = Field(description="Stable machine-readable error code")
    message: str = Field(description="Human-readable explanation")
    request_id: str | None = Field(None, examples=["req_3f9a1c2b7d4e"])
    details: dict = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Envelope used for all 4xx/5xx responses."""

    error: ErrorBody


# Every error code the API can return: (HTTP status, example message).
ERROR_CATALOG: dict[str, tuple[int, str]] = {
    "EMPTY_FILE": (400, "The uploaded file is empty."),
    "NO_EXTRACTABLE_TEXT": (400, "No extractable text found. The file may be scanned images or empty."),
    "CORRUPT_FILE": (400, "The file is damaged or password-protected and cannot be read."),
    "PROMPT_INJECTION_DETECTED": (
        400,
        "This request tries to change or reveal the assistant's instructions and was blocked.",
    ),
    "JAILBREAK_DETECTED": (400, "This request tries to bypass the assistant's rules and was blocked."),
    "NOT_AUTHENTICATED": (401, "Missing bearer token. Log in at /auth/login."),
    "NOT_FOUND": (404, "Document not found."),
    "DUPLICATE_DOCUMENT": (409, "This file was already uploaded as leave_policy.pdf."),
    "EMAIL_ALREADY_REGISTERED": (409, "An account with this email already exists."),
    "FILE_TOO_LARGE": (413, "File is larger than 20 MB."),
    "UNSUPPORTED_FILE_TYPE": (415, "Unsupported file type '.exe'."),
    "VALIDATION_ERROR": (422, "The request is invalid."),
    "INVALID_QUESTION": (422, "The question is empty."),
    "RATE_LIMITED": (429, "Rate limit exceeded (20/minute). Try again in 12 s."),
    "UPSTREAM_RATE_LIMITED": (429, "Cohere embeddings is rate-limiting requests. Try again shortly."),
    "UPSTREAM_UNAVAILABLE": (503, "Claude is not available right now. Try again shortly."),
}


def error_responses(*codes: str) -> dict:
    """OpenAPI ``responses`` with one realistic example per error code, grouped by HTTP status."""
    out: dict[int, dict] = {}
    for code in codes:
        status, message = ERROR_CATALOG[code]
        entry = out.setdefault(status, {"model": ErrorResponse, "content": {"application/json": {"examples": {}}}})
        entry["content"]["application/json"]["examples"][code] = {
            "summary": code,
            "value": {"error": {"code": code, "message": message, "request_id": "req_3f9a1c2b7d4e", "details": {}}},
        }
    return out


# ---------- auth ----------
class SignupRequest(BaseModel):
    """New account."""

    email: EmailStr = Field(examples=["sana@example.com"])
    password: str = Field(min_length=8, max_length=128, examples=["a-long-passphrase"])


class UserOut(BaseModel):
    """Public user data."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    created_at: datetime


class TokenOut(BaseModel):
    """JWT returned by login."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the token expires", examples=[3600])


# ---------- documents ----------
class DocumentOut(BaseModel):
    """An uploaded document."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str = Field(examples=["leave_policy.pdf"])
    file_type: str = Field(examples=["pdf"])
    size_bytes: int
    page_count: int | None = Field(None, description="Pages (PDF) or slides (PPTX); null for DOCX/TXT/MD")
    chunk_count: int
    flagged_chunks: int = Field(0, description="Chunks containing text that looks like instructions to an AI")
    empty_pages: list[int] = Field(default_factory=list, description="Pages with no extractable text")
    status: str = Field(examples=["ready"])
    created_at: datetime


class DocumentList(BaseModel):
    """Page of documents."""

    items: list[DocumentOut]
    total: int


# ---------- chat ----------
class AskRequest(BaseModel):
    """A question about the user's documents."""

    question: str = Field(min_length=1, max_length=1000, examples=["How many paid sick days do employees get?"])
    session_id: str | None = Field(None, description="Continue an existing chat session")
    document_ids: list[str] | None = Field(None, max_length=50, description="Only search these documents")


class CitationOut(BaseModel):
    """A validated citation; ``n`` matches the [n] marker in the answer."""

    n: int
    chunk_id: str
    document_id: str
    filename: str
    page_start: int | None
    page_end: int | None
    section: str | None
    cited_text: str


class RetrievedChunkOut(BaseModel):
    """A retrieved chunk with all its scores (shown in the evidence panel)."""

    chunk_id: str
    document_id: str
    filename: str
    position: int
    page_start: int | None
    page_end: int | None
    section: str | None
    text: str
    kind: str
    similarity: float | None = Field(description="Cosine similarity between question and chunk (dense embeddings)")
    dense_rank: int | None
    bm25_rank: int | None
    rrf_score: float
    rerank_score: float | None = Field(description="Calibrated 0-1 relevance from the reranker")
    kept: bool = Field(description="Passed the threshold and was sent to the LLM")
    cited: bool
    suspicious: bool = Field(description="Contains text that looks like instructions to an AI (treated as data)")


class UsageOut(BaseModel):
    """Token usage and estimated cost of one answer."""

    tokens_in: int
    tokens_out: int
    cost_usd: float


class AskResponse(BaseModel):
    """Answer (or refusal) with citations and the full retrieval evidence."""

    answer: str = Field(examples=["Employees get 10 paid sick days per year [1]."])
    refused: bool
    refusal_reason: str | None = Field(
        None, description="no_documents_uploaded | no_relevant_chunks | not_in_documents | off_topic | output_blocked"
    )
    language: str = Field(examples=["en"])
    session_id: str
    message_id: str
    citations: list[CitationOut]
    retrieved_count: int = Field(description="Number of candidate chunks fetched")
    kept_count: int = Field(description="Number of chunks that passed the threshold and were sent to the LLM")
    retrieved_chunks: list[RetrievedChunkOut]
    retrieval_config: str
    usage: UsageOut
    latency_ms: int
    timings_ms: dict[str, int]
    request_id: str


class MessageOut(BaseModel):
    """A stored chat message."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str
    refused: bool
    refusal_reason: str | None
    citations: list
    retrieved_chunks: list
    latency_ms: int | None
    created_at: datetime


class SessionOut(BaseModel):
    """A chat session with its messages."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    created_at: datetime
    messages: list[MessageOut] = []


class HistoryOut(BaseModel):
    """The user's chat history."""

    sessions: list[SessionOut]


# ---------- monitoring ----------
class HealthOut(BaseModel):
    """Service health."""

    status: str
    mode: str
    checks: dict[str, str] = Field(default_factory=dict)


class MetricsOut(BaseModel):
    """Aggregated metrics for the monitoring dashboard."""

    window_hours: int
    requests: int
    error_rate: float = Field(description="Share of requests that ended in 5xx")
    rate_limited: int
    questions: int
    refusal_rate: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    step_p50_ms: dict[str, float | None]
    avg_tokens_in: float | None
    avg_tokens_out: float | None
    avg_cost_usd: float | None
    total_cost_usd: float
    by_route: dict[str, dict[str, float]]
    timeline: list[dict]


class TraceOut(BaseModel):
    """One request with its step timings (embed, search, rerank, LLM)."""

    request_id: str = Field(examples=["req_3f9a1c2b7d4e"])
    created_at: datetime
    method: str
    route: str
    status_code: int
    latency_ms: int
    steps_ms: dict[str, int] = Field(examples=[{"embed_ms": 120, "search_ms": 35, "rerank_ms": 180, "llm_ms": 900}])
    tokens_in: int
    tokens_out: int
    cost_usd: float
    refused: bool | None
    error_type: str | None
