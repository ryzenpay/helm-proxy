# helm-proxy

Serve standard **Helm HTTP repositories** (`index.yaml` + `.tgz`) on demand from
**git repositories** containing charts. Built for the k3s/cattle
[`HelmChart`](https://docs.k3s.io/add-ons/helm) controller, but works with any Helm
client.

Point `spec.repo` at helm-proxy with the git repo encoded in the URL; helm-proxy
clones the repo, discovers charts, packages them, and serves the synthesized index.

```
spec.repo: https://helm-proxy.example.com/git/github.com/org/charts@main
```

## How it works

```
Helm / HelmChart controller
        │  GET /git/<host>/<org>/<repo>@<ref>/index.yaml
        ▼
   helm-proxy ──► git clone (shallow, TTL-cached)
        │         helm package  (Chart.yaml dirs)
        │         + committed *.tgz
        │         helm repo index --url <proxy base>
        ▼
   index.yaml + <chart>-<version>.tgz  ──► Helm
```

- **Dynamic URL mapping** — no per-repo registration. The git host/org/repo and ref
  live in the request path: `/git/<host>/<org>/<repo>[@ref]/...`. `@ref` defaults to
  `DEFAULT_REF` (`main`).
- **Auto-detects layout** — packages `Chart.yaml` source directories *and* indexes any
  committed `*.tgz`. Subcharts under a `charts/` directory are not published.
- **Versioning** — chart versions come from each chart's `Chart.yaml` on the requested
  ref.
- **Auth (pass-through *or* configured)** — client basic-auth (e.g. from a HelmChart
  `authSecret`) is forwarded to git; otherwise per-host credentials configured on the
  proxy are used; otherwise the clone is anonymous. Credentials are sent to git via an
  `Authorization` header — never embedded in URLs or logged.
- **Caching** — clones + packaged charts are cached for `CACHE_TTL` seconds
  (default 300). `POST /refresh?repo=...` forces a re-fetch.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness/readiness (suppressed from access logs) |
| GET | `/` | Usage hint |
| GET | `/git/{host}/{org}/{repo}@{ref}/index.yaml` | Repository index |
| GET | `/git/{host}/{org}/{repo}@{ref}/{chart}-{version}.tgz` | Packaged chart |
| POST | `/refresh?repo={host}/{org}/{repo}@{ref}` | Invalidate cache |

## Configuration

All via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `7713` | TCP port the server binds to |
| `CACHE_TTL` | `300` | Seconds before git is re-fetched |
| `CACHE_DIR` | `/var/cache/helm-proxy` | Clone + package storage |
| `DEFAULT_REF` | `main` | Ref used when `@ref` is omitted |
| `CLONE_DEPTH` | `1` | `git clone --depth` (0 = full) |
| `GIT_TIMEOUT` | `120` | Per-command timeout (seconds) |
| `EXTERNAL_BASE_URL` | *(empty)* | Public base URL; empty = derive from request (`X-Forwarded-*` honored) |
| `ALLOWED_HOSTS` | *(empty)* | Comma-separated allowlist of `host` / `host/org`. **Empty = deny all**; use `*` to allow ANY host |
| `GIT_CREDENTIALS` | `{}` | JSON: `{"host/org": {"username": "...", "password"\|"token": "..."}}` |
| `REFRESH_TOKEN` | *(empty)* | If set, `POST /refresh` requires `Authorization: Bearer <token>` |
| `LOG_LEVEL` | `info` | Log level |

> **Security:** In dynamic mode the proxy clones whatever git URL is requested, so it
> is restricted by `ALLOWED_HOSTS`. An empty allowlist denies everything;
> set specific `host` / `host/org` entries, or `*` to allow any host (not recommended).

## Run locally

```bash
pip install -r requirements.txt          # needs git + helm on PATH
python -m app                            # serves on :7713

helm repo add demo http://localhost:7713/git/github.com/org/charts@main
helm repo update
helm search repo demo
```

## Docker

```bash
docker compose up --build
# the image bundles git + the helm CLI
```

## Kubernetes (Helm chart)

```bash
helm install helm-proxy ./charts/helm-proxy \
  --set config.allowedHosts='{github.com,gitlab.com}' \
  --set config.externalBaseUrl=https://helm-proxy.example.com \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=helm-proxy.example.com
```

Key `values.yaml` knobs: `image.*`, `config.*` (TTL, allowlist, base URL),
`gitCredentials` (rendered into a Secret), `ingress.*`, `persistence.*`, `resources`.

## Use from k3s HelmChart

See [`examples/helmchart-k3s.yaml`](examples/helmchart-k3s.yaml) — it shows a private
git repo via an `authSecret` (`kubernetes.io/basic-auth`) passed through to git.

## Tests

```bash
pip install -e '.[test]'
pytest
```

Chart-packaging tests are skipped automatically if the `helm` CLI is not installed.
