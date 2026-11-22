"""API test fixtures: the real app with fake providers, SQLite and in-memory Qdrant.

Fake providers are deterministic and offline. They prove the plumbing (auth,
isolation, errors, citations, deletion); they say nothing about answer quality.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = {
        "_env_file": None,
        "use_fake_providers": True,
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}",
        "qdrant_url": None,
        "qdrant_path": None,
        "jwt_secret_key": "test-only-secret-key-not-used-anywhere-else",
        "rate_limit_ask": "1000/minute",
        "rate_limit_upload": "1000/minute",
        "rate_limit_auth": "1000/minute",
        "log_level": "WARNING",
    }
    return Settings(**{**base, **overrides})


@asynccontextmanager
async def running_app(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    async with running_app(make_settings(tmp_path)) as c:
        yield c


@pytest.fixture
def app_factory(tmp_path: Path) -> Callable:
    """Start an app with custom settings: ``async with app_factory(rate_limit_ask='2/minute') as c``."""

    def _factory(**overrides):
        return running_app(make_settings(tmp_path, **overrides))

    return _factory


async def auth_headers(client: httpx.AsyncClient, email: str = "sana@example.com") -> dict[str, str]:
    r = await client.post("/auth/signup", json={"email": email, "password": "a-long-passphrase"})
    assert r.status_code == 201, r.text
    r = await client.post("/auth/login", data={"username": email, "password": "a-long-passphrase"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def upload(client: httpx.AsyncClient, headers: dict, path: Path, name: str | None = None) -> httpx.Response:
    return await client.post("/documents", headers=headers, files={"file": (name or path.name, path.read_bytes())})


@pytest.fixture
async def user(client: httpx.AsyncClient) -> dict[str, str]:
    return await auth_headers(client)
