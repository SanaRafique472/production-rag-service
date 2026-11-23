"""Retries, timeouts and error translation for every external call.

Each provider call goes through ``call_external``:

* transient failures (timeouts, connection errors, HTTP 5xx, "overloaded") and
  rate limits are retried with exponential backoff and jitter;
* after the last attempt, rate limits become ``UpstreamRateLimitedError`` (429)
  and everything else becomes ``UpstreamUnavailableError`` (503);
* non-retryable errors (bad API key, invalid request) fail immediately as 503
  with a message that never contains the key itself.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.errors import AppError, UpstreamRateLimitedError, UpstreamUnavailableError

T = TypeVar("T")
log = logging.getLogger(__name__)

_RATE_NAMES = {"RateLimitError", "TooManyRequestsError"}
_TRANSIENT_NAMES = {
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "ServiceUnavailableError",
    "GatewayTimeoutError",
    "OverloadedError",
    "RequestTimeoutError",
    "DeadlineExceededError",
    "TimeoutException",
    "ConnectError",
    "ReadTimeout",
    "ConnectTimeout",
    "RemoteProtocolError",
    "ResponseHandlingException",
    "TimeoutError",
}


def classify(exc: BaseException) -> str:
    """Return 'rate', 'transient' or 'fatal' for an exception from any SDK."""
    names = {cls.__name__ for cls in type(exc).__mro__}
    if names & _RATE_NAMES or getattr(exc, "status_code", None) == 429:
        return "rate"
    if names & _TRANSIENT_NAMES or isinstance(exc, asyncio.TimeoutError | ConnectionError):
        return "transient"
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return "transient"
    return "fatal"


async def call_external(
    provider: str,
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    timeout_s: float = 30.0,
) -> T:
    """Run ``fn`` with a timeout and retries; translate failures to API errors."""

    async def guarded() -> T:
        return await asyncio.wait_for(fn(), timeout=timeout_s)

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential_jitter(multiplier=0.5, max=8),
            retry=retry_if_exception(lambda e: classify(e) in {"rate", "transient"}),
            reraise=False,
        ):
            with attempt:
                return await guarded()
    except RetryError as err:
        last = err.last_attempt.exception()
        log.warning("external call failed after retries", extra={"provider": provider})
        if last is not None and classify(last) == "rate":
            raise UpstreamRateLimitedError(f"{provider} is rate-limiting requests; try again shortly.") from last
        raise UpstreamUnavailableError(f"{provider} is unavailable or timing out.") from last
    except AppError:
        raise
    except Exception as exc:  # fatal: wrong key, bad request, etc.
        log.error("external call rejected", extra={"provider": provider, "error_type": type(exc).__name__})
        raise UpstreamUnavailableError(
            f"{provider} rejected the request ({type(exc).__name__}). Check configuration."
        ) from exc
    raise UpstreamUnavailableError(f"{provider} call did not complete.")  # pragma: no cover


class MissingKeyProvider:
    """Stand-in used when a provider key is not configured: every call fails with 503."""

    def __init__(self, provider: str, env_var: str) -> None:
        """Remember which variable is missing, for a clear error message."""
        self.provider, self.env_var = provider, env_var

    def __getattr__(self, name: str) -> Callable[..., Awaitable[None]]:
        """Return an async function that raises ``UpstreamUnavailableError``."""

        async def fail(*_: object, **__: object) -> None:
            raise UpstreamUnavailableError(f"{self.provider} is not configured: set {self.env_var}.")

        return fail
