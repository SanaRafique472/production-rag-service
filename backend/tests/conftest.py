"""Shared pytest fixtures."""

import uuid
from pathlib import Path

import pytest

from app.config import Settings
from tests.fixtures.samples import build_samples


@pytest.fixture(scope="session")
def samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Freshly generated sample documents, shared by the whole test session."""
    return build_samples(tmp_path_factory.mktemp("samples"))


@pytest.fixture
def settings() -> Settings:
    """Default settings, isolated from any local .env file."""
    return Settings(_env_file=None)


@pytest.fixture
def doc_id() -> str:
    """A random document id."""
    return str(uuid.uuid4())
