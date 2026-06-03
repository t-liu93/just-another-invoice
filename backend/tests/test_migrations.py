"""Tests for Alembic migrations – upgrade / downgrade cycle.

Runs ``alembic`` in a subprocess (because ``env.py`` calls ``asyncio.run``,
which would clash with pytest-asyncio's event loop).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from jai.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _alembic_url() -> str:
    """Derive a dedicated migration-test database URL."""
    from urllib.parse import urlparse, urlunparse

    settings = get_settings()
    parsed = urlparse(settings.database_url)
    return urlunparse(parsed._replace(path="/jai_test_migrations"))


def _run_alembic(*extra_args: str, url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"url={url}", *extra_args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )


def _ensure_database(url: str) -> None:
    """Create the test database if it doesn't exist (via maintenance DB)."""
    import asyncio
    from urllib.parse import urlparse, urlunparse

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    maint_url = urlunparse(parsed._replace(path="/postgres"))

    async def _create() -> None:
        engine = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
        async with engine.begin() as conn:
            # Terminate any existing connections
            await conn.execute(
                text(
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
                )
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        await engine.dispose()

    asyncio.run(_create())


def _drop_database(url: str) -> None:
    """Drop the test database."""
    import asyncio
    from urllib.parse import urlparse, urlunparse

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    maint_url = urlunparse(parsed._replace(path="/postgres"))

    async def _drop() -> None:
        engine = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
                )
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        await engine.dispose()

    asyncio.run(_drop())


class TestMigrations:
    """Verify ``alembic upgrade head`` and ``alembic downgrade base``."""

    @classmethod
    def setup_class(cls) -> None:
        cls.url = _alembic_url()
        _ensure_database(cls.url)

    @classmethod
    def teardown_class(cls) -> None:
        _drop_database(cls.url)

    def test_upgrade_head(self) -> None:
        """``alembic upgrade head`` should succeed."""
        result = _run_alembic("upgrade", "head", url=self.url)
        assert result.returncode == 0, (
            f"upgrade head failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_downgrade_base(self) -> None:
        """``alembic downgrade base`` should succeed after upgrade."""
        # First upgrade.
        result = _run_alembic("upgrade", "head", url=self.url)
        assert result.returncode == 0
        # Then downgrade.
        result = _run_alembic("downgrade", "base", url=self.url)
        assert result.returncode == 0, (
            f"downgrade base failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_upgrade_then_upgrade_is_idempotent(self) -> None:
        """Running ``upgrade head`` twice should be a no-op (not error)."""
        result = _run_alembic("upgrade", "head", url=self.url)
        assert result.returncode == 0
        result = _run_alembic("upgrade", "head", url=self.url)
        assert result.returncode == 0

    def test_downgrade_at_base_is_safe(self) -> None:
        """``downgrade base`` when already at base should not error."""
        result = _run_alembic("downgrade", "base", url=self.url)
        assert result.returncode == 0
        result = _run_alembic("downgrade", "base", url=self.url)
        assert result.returncode == 0
