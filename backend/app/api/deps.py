"""Shared FastAPI dependencies: DB session, container, current user, rate limits."""

import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_access_token
from app.db.models import User
from app.errors import AppError, AuthenticationError
from app.services.container import Container

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


class RateLimitedError(AppError):
    """Too many requests from one user or IP."""

    status_code = 429
    code = "RATE_LIMITED"


def get_container(request: Request) -> Container:
    """The clients built at startup."""
    return request.app.state.container


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One database session per request."""
    async with request.app.state.sessionmaker() as session:
        yield session


async def current_user(
    request: Request, token: str | None = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
) -> User:
    """Resolve the Bearer token to an active user (401 otherwise)."""
    if not token:
        raise AuthenticationError("Missing bearer token. Log in at /auth/login.")
    user_id = decode_access_token(token, request.app.state.container.settings)
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User no longer exists or is disabled.")
    request.state.user_id = user.id
    return user


_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def parse_rate(rate: str) -> tuple[int, int]:
    """Turn '20/minute' into (20, 60)."""
    count, unit = rate.split("/")
    return int(count), _UNITS[unit.strip().rstrip("s")]


class RateLimiter:
    """Sliding-window limiter kept in memory.

    Good for one API instance. With several instances behind a load balancer
    the counters must move to Redis so all instances share them.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, rate: str) -> None:
        """Record a hit or raise 429 with a Retry-After hint."""
        limit, window = parse_rate(rate)
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] >= window:
            hits.popleft()
        if len(hits) >= limit:
            retry = int(window - (now - hits[0])) + 1
            raise RateLimitedError(f"Rate limit exceeded ({rate}). Try again in {retry} s.", {"retry_after": retry})
        hits.append(now)


def rate_limit(name: str):  # noqa: ANN201
    """Dependency factory: limit per user when logged in, else per client IP."""

    async def _dep(request: Request, token: str | None = Depends(oauth2_scheme)) -> None:
        c: Container = request.app.state.container
        who = token[-24:] if token else (request.client.host if request.client else "anon")
        request.app.state.rate_limiter.check(f"{name}:{who}", getattr(c.settings, f"rate_limit_{name}"))

    return _dep
