"""Entrypoint: ``python -m app`` runs uvicorn with the health-filtered log config."""

from __future__ import annotations

import uvicorn

from .config import get_settings
from .logging_config import build_log_config

# Fixed bind port. Not configurable via env: Kubernetes injects a colliding
# HELM_PROXY_PORT (tcp://ip:port) service variable when the Service is named helm-proxy.
PORT = 8080


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=PORT,
        log_config=build_log_config(settings.log_level),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
