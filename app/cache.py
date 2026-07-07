"""TTL cache tying git clones to packaged Helm repositories.

One :class:`RepoCache` instance is shared by the app. It guarantees that concurrent
requests for the same ``(repo, ref)`` clone and package only once (per-key lock), and
that git is not re-hit more often than ``cache_ttl``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .auth import Credentials
from .git_backend import RepoSpec, ensure_repo
from .packager import build_repository

logger = logging.getLogger("helm_proxy.cache")


@dataclass
class RepoArtifacts:
    """A built repository: the directory of .tgz files and the index.yaml bytes."""

    package_dir: Path
    index_bytes: bytes


@dataclass
class _Entry:
    artifacts: RepoArtifacts
    base_url: str
    built_at: float


class RepoCache:
    """Caches packaged repositories with a TTL and per-key locking."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        ttl: int,
        clone_depth: int,
        timeout: int,
    ) -> None:
        self._cache_dir = cache_dir
        self._ttl = ttl
        self._clone_depth = clone_depth
        self._timeout = timeout
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def _is_fresh(self, entry: _Entry, base_url: str) -> bool:
        return (
            entry.base_url == base_url
            and (time.monotonic() - entry.built_at) < self._ttl
        )

    async def get(
        self, spec: RepoSpec, creds: Credentials | None, base_url: str
    ) -> RepoArtifacts:
        """Return packaged artifacts for ``spec``, cloning/packaging if needed."""
        key = spec.cache_key
        entry = self._entries.get(key)
        if entry and self._is_fresh(entry, base_url):
            return entry.artifacts

        lock = await self._lock_for(key)
        async with lock:
            # Re-check: another coroutine may have built while we waited.
            entry = self._entries.get(key)
            if entry and self._is_fresh(entry, base_url):
                return entry.artifacts
            return await self._build(spec, creds, base_url, key)

    async def _build(
        self, spec: RepoSpec, creds: Credentials | None, base_url: str, key: str
    ) -> RepoArtifacts:
        repo_path = await ensure_repo(
            spec,
            creds,
            self._cache_dir,
            depth=self._clone_depth,
            timeout=self._timeout,
        )
        package_dir = self._cache_dir / "packages" / key
        index_bytes = await build_repository(
            repo_path, package_dir, base_url, timeout=self._timeout
        )
        artifacts = RepoArtifacts(package_dir=package_dir, index_bytes=index_bytes)
        self._entries[key] = _Entry(
            artifacts=artifacts, base_url=base_url, built_at=time.monotonic()
        )
        return artifacts

    def invalidate(self, spec: RepoSpec) -> bool:
        """Drop the cached entry for ``spec``. Returns True if one existed."""
        return self._entries.pop(spec.cache_key, None) is not None
