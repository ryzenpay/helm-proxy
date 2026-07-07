"""Entrypoint: ``python -m app`` runs uvicorn with the health-filtered log config."""

from __future__ import annotations

import os

import uvicorn

from .config import get_settings
from .logging_config import build_log_config


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HELM_PROXY_HOST", "0.0.0.0"),
        port=int(os.environ.get("HELM_PROXY_PORT", "8080")),
        log_config=build_log_config(settings.log_level),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
