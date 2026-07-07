"""Authentication, credential resolution, and host allowlisting.

Two independent concerns live here:

* **Host allowlist** — decide whether a git host may be proxied at all (SSRF guard).
* **Credential resolution** — figure out which basic-auth credentials to hand to git,
  preferring credentials supplied by the client over any configured on the proxy.

Credentials are represented as a ``(username, password)`` tuple and are injected into
git via an ``Authorization: Basic`` header (see :mod:`app.git_backend`) so they never
appear in a clone URL, the process list, or logs.
"""

from __future__ import annotations

import base64
import binascii
import logging

from starlette.requests import Request

from .config import Settings

logger = logging.getLogger("helm_proxy.auth")

Credentials = tuple[str, str]

# Ensures the allow-all warning is only emitted once per process.
_warned_allow_all = False


def host_allowed(host: str, path: str, settings: Settings) -> bool:
    """Return True if host/path may be proxied (entries are host or host/org prefixes).
    Empty allowlist allows everything but logs a one-time warning.
    """
    global _warned_allow_all
    if not settings.allowed_hosts:
        if not _warned_allow_all:
            logger.warning(
                "HELM_PROXY_ALLOWED_HOSTS is empty: proxying ANY git host. "
                "Set an allowlist in production to prevent SSRF."
            )
            _warned_allow_all = True
        return True

    candidate = f"{host}/{path}".strip("/")
    for entry in settings.allowed_hosts:
        entry = entry.strip("/")
        if candidate == entry or candidate.startswith(entry + "/"):
            return True
    return False


def _decode_basic_auth(header: str) -> Credentials | None:
    """Decode an ``Authorization: Basic`` header into (user, password)."""
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


def _match_configured(host: str, path: str, settings: Settings) -> Credentials | None:
    """Find the longest ``host[/org]`` credential prefix that matches the request."""
    candidate = f"{host}/{path}".strip("/")
    best: tuple[int, Credentials] | None = None
    for prefix, creds in settings.git_credentials.items():
        prefix_key = prefix.strip("/")
        if candidate == prefix_key or candidate.startswith(prefix_key + "/"):
            username = creds.get("username", "")
            # A {"token": ...} is accepted as password, or as username when no user set.
            password = creds.get("password", creds.get("token", ""))
            if not username and "token" in creds:
                username = creds["token"]
            if best is None or len(prefix_key) > best[0]:
                best = (len(prefix_key), (username, password))
    return best[1] if best else None


def resolve_credentials(
    request: Request, host: str, path: str, settings: Settings
) -> Credentials | None:
    """Resolve git credentials, preferring the client's basic-auth header.
    Falls back to proxy-configured host[/org] credentials, else None (anonymous).
    """
    header = request.headers.get("authorization")
    if header:
        creds = _decode_basic_auth(header)
        if creds:
            return creds
    return _match_configured(host, path, settings)
