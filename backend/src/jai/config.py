"""Application settings loaded from environment variables via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


def _find_env_file() -> str:
    """Search upward from *this file* for a repo-root ``.env``.

    The repo root is identified by ``docker-compose.yml`` being present.
    Falls back to ``".env"`` (CWD-relative) so that pydantic-settings
    gracefully degrades when the marker file is absent (e.g. in CI where
    ``DATABASE_URL`` is set as a plain environment variable).
    """
    start = Path(__file__).resolve().parent
    for p in [start, *start.parents]:
        if (p / "docker-compose.yml").is_file():
            return str(p / ".env")
    return ".env"


class Settings(BaseSettings):
    """Central configuration.  All fields have sensible defaults.

    **Priority (high → low)**:
      1. OS environment variables  (``POSTGRES_HOST=...``)
      2. ``.env`` file in the repo root  (found via :func:`_find_env_file`)
      3. Code defaults below

    The database connection is configured via individual ``POSTGRES_*`` fields.
    ``database_url`` is auto-assembled from those fields.  If you need full
    control, set ``DATABASE_URL`` directly — it takes precedence over the
    individual parts.

    Typical overrides:
      - **Local dev**: ``POSTGRES_PORT=5433`` in ``.env``
        (container mapped to host port 5433, rest uses defaults).
      - **Production Docker**: ``POSTGRES_HOST=postgres`` in Compose env
        (internal network, rest uses defaults).
    """

    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- PostgreSQL connection parts -----------------------------------------
    # These double as the postgres container config (USER/PASSWORD/DB) and
    # the backend's connection parameters.  In most cases you only need to
    # override HOST and PORT.
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "jai"
    postgres_password: str = "jai"
    postgres_db: str = "jai"

    # -- Full URL override (escape hatch) -----------------------------------
    # If set, takes precedence over the individual POSTGRES_* fields above.
    database_url: str | None = None

    # -- Frontend static files (deployment mode) ----------------------------
    # When set, FastAPI serves the SPA from this directory.
    static_dir: str | None = None

    # -- Application metadata -----------------------------------------------
    app_version: str = "0.1.0"

    @model_validator(mode="after")
    def _assemble_database_url(self) -> Settings:
        """Build ``database_url`` from parts if not explicitly provided.

        Uses ``URL.create()`` so that special characters in the password
        (``@``, ``:``, ``/``, ``#`` …) are safely percent-encoded.
        """
        if self.database_url is None:
            url = URL.create(
                drivername="postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password,
                host=self.postgres_host,
                port=self.postgres_port,
                database=self.postgres_db,
            )
            self.database_url = url.render_as_string(hide_password=False)
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` singleton."""
    return Settings()
