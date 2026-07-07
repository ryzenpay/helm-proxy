"""Git clone/fetch backend.

Responsible for turning a ``RepoSpec`` (host + path + ref) into a checked-out working
tree on local disk, using credentials without ever leaking them.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from .auth import Credentials

logger = logging.getLogger("helm_proxy.git")


class GitError(RuntimeError):
    """Raised when a git command fails."""


@dataclass(frozen=True)
class RepoSpec:
    """A parsed request target: which git repo and ref to serve."""

    host: str          # e.g. "github.com"
    path: str          # e.g. "org/charts" (no leading slash, no .git)
    ref: str           # branch/tag/sha

    @property
    def clone_url(self) -> str:
        """HTTPS clone URL for this repo."""
        return f"https://{self.host}/{self.path}.git"

    @property
    def cache_key(self) -> str:
        """Stable filesystem-safe key for (url, ref)."""
        raw = f"{self.clone_url}@{self.ref}".encode()
        return hashlib.sha256(raw).hexdigest()[:32]


def parse_repo_spec(repo_spec: str, default_ref: str) -> RepoSpec:
    """Parse a URL-encoded ``host/org/repo[@ref]`` string into a :class:`RepoSpec`.

    Raises :class:`ValueError` on obviously malformed or unsafe input.
    """
    spec = repo_spec.strip().strip("/")
    if not spec:
        raise ValueError("empty repository spec")

    # Reject path traversal / schemes before doing anything else.
    if ".." in spec.split("/") or "://" in spec:
        raise ValueError("invalid repository spec")

    ref = default_ref
    if "@" in spec:
        spec, _, ref = spec.rpartition("@")
        ref = ref.strip("/")
        if not ref:
            ref = default_ref

    spec = spec.strip("/")
    if spec.endswith(".git"):
        spec = spec[: -len(".git")]

    parts = spec.split("/")
    if len(parts) < 2:
        raise ValueError("repository spec must be host/org/repo")

    host = parts[0]
    path = "/".join(parts[1:])
    if not host or not path:
        raise ValueError("repository spec must include a host and a path")
    return RepoSpec(host=host, path=path, ref=ref)


def _auth_config_args(creds: Credentials | None) -> list[str]:
    """Return ``-c http.extraHeader=...`` args carrying basic-auth, if any."""
    if not creds:
        return []
    username, password = creds
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    # Value is passed via argv; git forwards it as an HTTP header on fetch.
    return ["-c", f"http.extraHeader=Authorization: Basic {token}"]


def _scrub(text: str, creds: Credentials | None) -> str:
    """Remove any occurrence of the secret from surfaced error text."""
    if creds and creds[1]:
        text = text.replace(creds[1], "***")
    return text


async def _run_git(
    args: list[str], *, cwd: Path | None, creds: Credentials | None, timeout: int
) -> str:
    """Run a git command, returning stdout; raise :class:`GitError` on failure."""
    cmd = ["git", *_auth_config_args(creds), *args]
    # Never prompt interactively; fail fast instead of hanging.
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "PATH": _default_path(),
    }
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise GitError(f"git command timed out after {timeout}s") from exc

    if proc.returncode != 0:
        detail = _scrub(stderr.decode(errors="replace").strip(), creds)
        # Redact the header arg from the echoed command as well.
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return stdout.decode(errors="replace")


def _default_path() -> str:
    import os

    return os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")


async def ensure_repo(
    spec: RepoSpec,
    creds: Credentials | None,
    cache_dir: Path,
    *,
    depth: int,
    timeout: int,
) -> Path:
    """Clone (or fetch + hard-reset) ``spec`` and return its checkout path.
    Always hits the remote; callers own TTL and locking.
    """
    repo_path = cache_dir / "repos" / spec.cache_key
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    if (repo_path / ".git").exists():
        await _fetch_existing(repo_path, spec, creds, depth=depth, timeout=timeout)
        return repo_path

    await _clone_fresh(repo_path, spec, creds, depth=depth, timeout=timeout)
    return repo_path


async def _clone_fresh(
    repo_path: Path,
    spec: RepoSpec,
    creds: Credentials | None,
    *,
    depth: int,
    timeout: int,
) -> None:
    if repo_path.exists():
        shutil.rmtree(repo_path)
    args = ["clone"]
    if depth and depth > 0:
        args += ["--depth", str(depth)]
    args += ["--branch", spec.ref, spec.clone_url, str(repo_path)]
    try:
        await _run_git(args, cwd=None, creds=creds, timeout=timeout)
    except GitError:
        # --branch fails if ref is a commit sha; retry with a full clone + checkout.
        logger.info("branch clone failed for %s, retrying full clone", spec.clone_url)
        if repo_path.exists():
            shutil.rmtree(repo_path)
        await _run_git(
            ["clone", spec.clone_url, str(repo_path)], cwd=None, creds=creds,
            timeout=timeout,
        )
        await _run_git(
            ["checkout", spec.ref], cwd=repo_path, creds=creds, timeout=timeout
        )


async def _fetch_existing(
    repo_path: Path,
    spec: RepoSpec,
    creds: Credentials | None,
    *,
    depth: int,
    timeout: int,
) -> None:
    fetch = ["fetch", "origin", spec.ref]
    if depth and depth > 0:
        fetch += ["--depth", str(depth)]
    await _run_git(fetch, cwd=repo_path, creds=creds, timeout=timeout)
    await _run_git(
        ["reset", "--hard", "FETCH_HEAD"], cwd=repo_path, creds=creds, timeout=timeout
    )
