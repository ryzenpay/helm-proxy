"""Tests for host allowlisting, credential resolution, and repo-spec parsing."""

from __future__ import annotations

import base64

import pytest
from starlette.requests import Request

from app.auth import host_allowed, resolve_credentials
from app.config import Settings
from app.git_backend import parse_repo_spec


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "method": "GET", "path": "/"})


# --- host_allowed -----------------------------------------------------------

def test_deny_all_when_empty():
    s = Settings(allowed_hosts=[])
    assert host_allowed("evil.example.com", "any/repo", s) is False


def test_wildcard_allows_all():
    s = Settings(allowed_hosts=["*"])
    assert host_allowed("evil.example.com", "any/repo", s) is True


def test_default_deny_when_configured():
    s = Settings(allowed_hosts=["github.com"])
    assert host_allowed("github.com", "org/repo", s) is True
    assert host_allowed("gitlab.com", "org/repo", s) is False


def test_host_org_prefix_match():
    s = Settings(allowed_hosts=["github.com/myorg"])
    assert host_allowed("github.com", "myorg/charts", s) is True
    assert host_allowed("github.com", "otherorg/charts", s) is False


# --- resolve_credentials ----------------------------------------------------

def test_client_basic_auth_passthrough():
    token = base64.b64encode(b"alice:secret").decode()
    req = _request({"authorization": f"Basic {token}"})
    s = Settings()
    assert resolve_credentials(req, "github.com", "org/repo", s) == ("alice", "secret")


def test_configured_credentials_used_when_no_header():
    s = Settings(git_credentials={"github.com/org": {"username": "bot", "token": "t0k"}})
    req = _request()
    assert resolve_credentials(req, "github.com", "org/repo", s) == ("bot", "t0k")


def test_client_header_beats_configured():
    token = base64.b64encode(b"alice:secret").decode()
    req = _request({"authorization": f"Basic {token}"})
    s = Settings(git_credentials={"github.com": {"username": "bot", "token": "t0k"}})
    assert resolve_credentials(req, "github.com", "org/repo", s) == ("alice", "secret")


def test_longest_prefix_wins():
    s = Settings(
        git_credentials={
            "github.com": {"username": "generic", "password": "g"},
            "github.com/org": {"username": "specific", "password": "s"},
        }
    )
    req = _request()
    assert resolve_credentials(req, "github.com", "org/repo", s) == ("specific", "s")


def test_no_credentials_returns_none():
    assert resolve_credentials(_request(), "github.com", "org/repo", Settings()) is None


# --- parse_repo_spec --------------------------------------------------------

def test_parse_basic():
    spec = parse_repo_spec("github.com/org/charts@develop", "main")
    assert (spec.host, spec.path, spec.ref) == ("github.com", "org/charts", "develop")
    assert spec.clone_url == "https://github.com/org/charts.git"


def test_parse_default_ref():
    spec = parse_repo_spec("github.com/org/charts", "main")
    assert spec.ref == "main"


def test_parse_strips_git_suffix():
    spec = parse_repo_spec("github.com/org/charts.git@v1", "main")
    assert spec.path == "org/charts"


@pytest.mark.parametrize("bad", ["", "single", "github.com/../etc", "http://x/y/z"])
def test_parse_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_repo_spec(bad, "main")


def test_cache_key_stable_and_ref_sensitive():
    a = parse_repo_spec("github.com/org/charts@main", "main")
    b = parse_repo_spec("github.com/org/charts@main", "main")
    c = parse_repo_spec("github.com/org/charts@dev", "main")
    assert a.cache_key == b.cache_key
    assert a.cache_key != c.cache_key
