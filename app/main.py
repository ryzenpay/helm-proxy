"""FastAPI application: serve git-backed Helm repositories.

Route summary::

    GET  /                               -> usage info
    GET  /health                         -> liveness (access-log suppressed)
    GET  /git/{repo_spec:path}/index.yaml
    GET  /git/{repo_spec:path}/{filename}   (serves *.tgz)
    POST /refresh?repo=<repo_spec>       -> invalidate cache for a repo
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from . import __version__
from .auth import host_allowed, resolve_credentials
from .cache import RepoCache
from .config import Settings, get_settings
from .git_backend import GitError, RepoSpec, parse_repo_spec
from .packager import PackagingError

logger = logging.getLogger("helm_proxy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the shared repo cache on startup."""
    settings = get_settings()
    cache_dir = Path(settings.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    app.state.cache = RepoCache(
        cache_dir,
        ttl=settings.cache_ttl,
        clone_depth=settings.clone_depth,
        timeout=settings.git_timeout,
    )
    logger.info(
        "helm-proxy %s started (cache_dir=%s ttl=%ss allowlist=%s)",
        __version__,
        cache_dir,
        settings.cache_ttl,
        settings.allowed_hosts or "ALLOW-ALL",
    )
    yield


app = FastAPI(title="helm-proxy", version=__version__, lifespan=lifespan)


def public_base(request: Request, settings: Settings) -> str:
    """Return the externally-visible base URL (scheme://host), honoring proxies."""
    if settings.external_base_url:
        return settings.external_base_url.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host"))
    if not host:
        host = request.url.netloc
    return f"{proto}://{host}"


def _parse_or_400(repo_spec: str, settings: Settings) -> RepoSpec:
    try:
        return parse_repo_spec(repo_spec, settings.default_ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    """Liveness/readiness probe."""
    return JSONResponse({"status": "ok"})


@app.get("/", include_in_schema=False)
async def index() -> PlainTextResponse:
    """Human-readable usage hint."""
    return PlainTextResponse(
        "helm-proxy: serve Helm repositories backed by git.\n\n"
        "Add a repo with:\n"
        "  helm repo add myrepo "
        "https://<this-host>/git/<git-host>/<org>/<repo>@<ref>\n\n"
        "Example:\n"
        "  https://<this-host>/git/github.com/org/charts@main\n"
    )


@app.get("/git/{repo_spec:path}/index.yaml")
async def get_index(
    request: Request,
    repo_spec: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Serve the synthesized index.yaml for a git repo."""
    spec = _parse_or_400(repo_spec, settings)
    if not host_allowed(spec.host, spec.path, settings):
        raise HTTPException(status_code=403, detail=f"host not allowed: {spec.host}")

    creds = resolve_credentials(request, spec.host, spec.path, settings)
    base_url = f"{public_base(request, settings)}/git/{repo_spec}"
    cache: RepoCache = request.app.state.cache
    try:
        artifacts = await cache.get(spec, creds, base_url)
    except GitError as exc:
        raise HTTPException(status_code=502, detail=f"git error: {exc}") from exc
    except PackagingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(
        content=artifacts.index_bytes,
        media_type="application/x-yaml",
        headers={"Cache-Control": f"public, max-age={settings.cache_ttl}"},
    )


@app.get("/git/{repo_spec:path}/{filename}")
async def get_chart(
    request: Request,
    repo_spec: str,
    filename: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Serve a packaged chart (.tgz) for a git repo."""
    if not filename.endswith(".tgz") or "/" in filename or filename.startswith("."):
        raise HTTPException(status_code=404, detail="not found")

    spec = _parse_or_400(repo_spec, settings)
    if not host_allowed(spec.host, spec.path, settings):
        raise HTTPException(status_code=403, detail=f"host not allowed: {spec.host}")

    creds = resolve_credentials(request, spec.host, spec.path, settings)
    base_url = f"{public_base(request, settings)}/git/{repo_spec}"
    cache: RepoCache = request.app.state.cache
    try:
        artifacts = await cache.get(spec, creds, base_url)
    except GitError as exc:
        raise HTTPException(status_code=502, detail=f"git error: {exc}") from exc
    except PackagingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    tgz_path = (artifacts.package_dir / filename).resolve()
    # Ensure the resolved path stays inside the package dir (defense in depth).
    if not str(tgz_path).startswith(str(artifacts.package_dir.resolve())):
        raise HTTPException(status_code=404, detail="not found")
    if not tgz_path.is_file():
        raise HTTPException(status_code=404, detail=f"chart not found: {filename}")

    return FileResponse(
        tgz_path, media_type="application/gzip", filename=filename
    )


@app.post("/refresh", include_in_schema=True)
async def refresh(
    request: Request,
    repo: str = Query(..., description="repo_spec to invalidate, e.g. host/org/repo@ref"),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Invalidate the cache for a repo so the next request re-fetches git."""
    if settings.refresh_token:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {settings.refresh_token}":
            raise HTTPException(status_code=401, detail="invalid refresh token")

    spec = _parse_or_400(repo, settings)
    cache: RepoCache = request.app.state.cache
    invalidated = cache.invalidate(spec)
    return JSONResponse({"invalidated": invalidated, "repo": f"{spec.host}/{spec.path}@{spec.ref}"})
