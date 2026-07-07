"""Environment-driven configuration for helm-proxy.

Each field declares its literal env var name via ``validation_alias`` so the name is
greppable (e.g. search ``DEFAULT_REF``).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration populated from environment variables."""

    # populate_by_name lets code/tests construct Settings(field=...) by field name too.
    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")

    # --- server ---
    port: int = Field(
        default=7713,
        validation_alias="PORT",
        description="TCP port the server binds to.",
    )

    # --- caching ---
    cache_ttl: int = Field(
        default=300,
        validation_alias="CACHE_TTL",
        description="Seconds a packaged repo is served before git is re-fetched.",
    )
    cache_dir: str = Field(
        default="/var/cache/helm-proxy",
        validation_alias="CACHE_DIR",
        description="Directory where git clones and packaged charts are stored.",
    )

    # --- git backend ---
    default_ref: str = Field(
        default="main",
        validation_alias="DEFAULT_REF",
        description="Git ref used when the request does not specify one via @ref.",
    )
    clone_depth: int = Field(
        default=1,
        validation_alias="CLONE_DEPTH",
        description="Depth passed to `git clone --depth`. 0 = full clone.",
    )
    git_timeout: int = Field(
        default=120,
        validation_alias="GIT_TIMEOUT",
        description="Timeout (seconds) for individual git/helm commands.",
    )

    # --- routing / URLs ---
    external_base_url: str = Field(
        default="",
        validation_alias="EXTERNAL_BASE_URL",
        description=(
            "Public base URL of the proxy (e.g. https://helm-proxy.example.com). "
            "When empty, URLs are derived from the incoming request "
            "(honoring X-Forwarded-* headers)."
        ),
    )

    # --- security ---
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias="ALLOWED_HOSTS",
        description=(
            "Allowlist of git hosts or host/org prefixes that may be proxied. "
            "Empty denies all; '*' allows any host."
        ),
    )
    git_credentials: Annotated[dict[str, dict[str, str]], NoDecode] = Field(
        default_factory=dict,
        validation_alias="GIT_CREDENTIALS",
        description=(
            "Map of 'host' or 'host/org' -> {username, password} (or {token}) used to "
            "authenticate git when the client supplies no basic-auth."
        ),
    )
    refresh_token: str = Field(
        default="",
        validation_alias="REFRESH_TOKEN",
        description="If set, POST /refresh requires this bearer token.",
    )

    # --- logging ---
    log_level: str = Field(
        default="info",
        validation_alias="LOG_LEVEL",
        description="Root/uvicorn log level.",
    )

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        """Accept a comma-separated string or a JSON list for allowed_hosts."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            if value.startswith("["):
                return json.loads(value)
            return [h.strip() for h in value.split(",") if h.strip()]
        return value

    @field_validator("git_credentials", mode="before")
    @classmethod
    def _parse_credentials(cls, value: object) -> object:
        """Accept a JSON string for git_credentials."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return {}
            return json.loads(value)
        return value


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
