"""Application errors with one consistent JSON shape.

Business code raises these exceptions; a single FastAPI exception handler
(added in Phase 4) converts them into::

    {"error": {"code": ..., "message": ..., "request_id": ..., "details": {...}}}

Keeping the HTTP status on the exception means routes never build error
responses by hand.
"""

from typing import Any


class AppError(Exception):
    """Base class for expected, user-facing errors."""

    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        """Store a human-readable message and optional structured details."""
        super().__init__(message)
        self.message = message
        self.details = details or {}


class EmptyFileError(AppError):
    """The uploaded file has zero bytes."""

    status_code, code = 400, "EMPTY_FILE"


class NoExtractableTextError(AppError):
    """The file parsed, but contains no usable text (e.g. a scanned PDF)."""

    status_code, code = 400, "NO_EXTRACTABLE_TEXT"


class CorruptFileError(AppError):
    """The file claims a supported type but cannot be parsed safely."""

    status_code, code = 400, "CORRUPT_FILE"


class FileTooLargeError(AppError):
    """The file exceeds the size or page limit."""

    status_code, code = 413, "FILE_TOO_LARGE"


class UnsupportedFileTypeError(AppError):
    """Unsupported extension, or content that does not match its extension."""

    status_code, code = 415, "UNSUPPORTED_FILE_TYPE"


class DuplicateDocumentError(AppError):
    """The same file content was already uploaded by this user."""

    status_code, code = 409, "DUPLICATE_DOCUMENT"


class NotFoundError(AppError):
    """Resource does not exist or is not owned by the caller."""

    status_code, code = 404, "NOT_FOUND"


class UpstreamUnavailableError(AppError):
    """An external dependency (LLM, embeddings, vector DB) failed after retries."""

    status_code, code = 503, "UPSTREAM_UNAVAILABLE"


class UpstreamRateLimitedError(AppError):
    """An external provider kept rate-limiting us after retries."""

    status_code, code = 429, "UPSTREAM_RATE_LIMITED"


class InvalidQuestionError(AppError):
    """Question is empty after trimming or otherwise invalid."""

    status_code, code = 422, "INVALID_QUESTION"


class PromptInjectionError(AppError):
    """The question tries to override or reveal the system instructions."""

    status_code, code = 400, "PROMPT_INJECTION_DETECTED"


class JailbreakError(AppError):
    """The question tries to remove the assistant's rules (role-play, 'DAN', ...)."""

    status_code, code = 400, "JAILBREAK_DETECTED"


class AuthenticationError(AppError):
    """Missing, invalid or expired credentials."""

    status_code, code = 401, "NOT_AUTHENTICATED"


class EmailTakenError(AppError):
    """Signup with an email that already has an account."""

    status_code, code = 409, "EMAIL_ALREADY_REGISTERED"
