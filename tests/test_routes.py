"""Route tests using a fake cache (no real git/helm needed)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cache import RepoArtifacts
from app.main import app


class FakeCache:
    """Stand-in for RepoCache that serves a prebuilt package dir."""

    def __init__(self, package_dir: Path, index_bytes: bytes) -> None:
        self.artifacts = RepoArtifacts(package_dir=package_dir, index_bytes=index_bytes)
        self.calls: list = []

    async def get(self, spec, creds, base_url):
        self.calls.append((spec, creds, base_url))
        return self.artifacts

    def invalidate(self, spec) -> bool:
        return True


@pytest.fixture
def client(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "web-1.0.0.tgz").write_bytes(b"tgz-bytes")
    index = b"apiVersion: v1\nentries:\n  web:\n  - version: 1.0.0\n"
    fake = FakeCache(pkg, index)
    with TestClient(app) as c:
        app.state.cache = fake
        c.fake = fake  # type: ignore[attr-defined]
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_index_yaml(client):
    r = client.get("/git/github.com/org/charts@main/index.yaml")
    assert r.status_code == 200
    assert b"entries" in r.content
    # base_url passed to the packager includes the full repo_spec path.
    _, _, base_url = client.fake.calls[-1]
    assert base_url.endswith("/git/github.com/org/charts@main")


def test_download_tgz(client):
    r = client.get("/git/github.com/org/charts@main/web-1.0.0.tgz")
    assert r.status_code == 200
    assert r.content == b"tgz-bytes"


def test_missing_tgz_is_404(client):
    r = client.get("/git/github.com/org/charts@main/nope-9.9.9.tgz")
    assert r.status_code == 404


def test_disallowed_host_is_403(client):
    # bitbucket.org is not in the test allowlist (github.com,gitlab.com).
    r = client.get("/git/bitbucket.org/org/charts@main/index.yaml")
    assert r.status_code == 403


def test_bad_spec_is_400(client):
    r = client.get("/git/singleword/index.yaml")
    assert r.status_code == 400


def test_refresh(client):
    r = client.post("/refresh", params={"repo": "github.com/org/charts@main"})
    assert r.status_code == 200
    assert r.json()["invalidated"] is True
