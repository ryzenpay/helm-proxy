"""Shared test fixtures and environment setup."""

from __future__ import annotations

import os
import tempfile

# Configure the app via env BEFORE it is imported anywhere.
_TMP_CACHE = tempfile.mkdtemp(prefix="helm-proxy-test-")
os.environ.setdefault("CACHE_DIR", _TMP_CACHE)
os.environ.setdefault("ALLOWED_HOSTS", "github.com,gitlab.com")

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Ensure each test sees a fresh Settings instance if env changed."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
