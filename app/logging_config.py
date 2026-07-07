"""Logging configuration with /health access-log suppression.

Exposes :func:`build_log_config`, a uvicorn-compatible ``logging`` dict that installs
a filter dropping access-log records for the ``/health`` endpoint. Health checks are
frequent and would otherwise drown the access log.
"""

from __future__ import annotations

import logging


class HealthCheckFilter(logging.Filter):
    """Drop uvicorn access-log records whose request path is /health."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        # uvicorn.access records carry args =
        #   (client_addr, method, full_path, http_version, status_code)
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            request_line = str(args[2])
            path = request_line.split("?", 1)[0]
            if path == "/health":
                return False
        return True


def build_log_config(level: str = "info") -> dict:
    """Return a uvicorn ``log_config`` dict with the health-check filter applied."""
    level = level.upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "health": {"()": "app.logging_config.HealthCheckFilter"},
        },
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s %(levelname)s: %(message)s",
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(asctime)s %(levelname)s access: %(client_addr)s '
                '"%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "filters": ["health"],
            },
        },
        "loggers": {
            "": {"handlers": ["default"], "level": level},
            "helm_proxy": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.error": {"level": level},
            "uvicorn.access": {
                "handlers": ["access"],
                "level": level,
                "propagate": False,
            },
        },
    }
