"""Structured JSON logging with request ids and secret redaction.

Every log line is one JSON object with the request id, so all lines for one
request can be found together. A filter redacts anything that looks like an
API key, password or JWT before it is written.
"""

import json
import logging
import sys
from contextvars import ContextVar

from app.guardrails import redact_secrets

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_EXTRA_KEYS = ("method", "path", "status", "latency_ms", "provider", "error_type", "user_id", "document_id")


class JsonFormatter(logging.Formatter):
    """Render records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Build the JSON line."""
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "msg": record.getMessage(),
        }
        data.update({k: getattr(record, k) for k in _EXTRA_KEYS if hasattr(record, k)})
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info).splitlines()[-1]
        return redact_secrets(json.dumps(data, ensure_ascii=False, default=str))


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
