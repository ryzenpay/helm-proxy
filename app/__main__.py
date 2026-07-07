"""Entrypoint: ``python -m app`` runs uvicorn with the health-filtered log config."""

from __future__ import annotations

import uvicorn

from .config import get_settings
from .logging_config import build_log_config


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_config=build_log_config(settings.log_level),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
