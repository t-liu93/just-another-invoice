"""Shared pytest fixtures.

Per-test isolation via a fresh PostgreSQL database. Schema is built by running
``alembic upgrade head`` once per session into a *template* database; each test
receives a database created via ``CREATE DATABASE ... TEMPLATE ...``, which is
near-instant on PostgreSQL.  The app's ``get_session`` dependency is overridden
so every request lands in the test database.

This mirrors the trading-journal conftest (sqlite file-copy) adapted for
PostgreSQL's template-database mechanism.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# The test client talks to the app over plain HTTP via ASGITransport, where
# (unlike a browser on localhost) ``Secure`` cookies are not echoed back. Pin
# COOKIE_SECURE=false BEFORE importing the app so the cookie transports — built
# once at import time — never set the Secure flag. This keeps the suite
# deterministic regardless of the developer's .env (which may set it true for
# HTTPS deployments).  Must run before any ``jai`` import.
os.environ["COOKIE_SECURE"] = "false"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import URL  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from jai.config import get_settings  # noqa: E402
from jai.db import get_session  # noqa: E402
from jai.main import app  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent

#: Database name used for the session-scoped template.
TEMPLATE_DB_NAME = "jai_test_template"

#: Counter to generate unique per-test database names.
_db_counter = 0


def _url_for_database(url: str, dbname: str) -> str:
    """Return *url* pointed at *dbname* without changing its role."""
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{dbname}"))


def _get_test_db_url(dbname: str) -> str:
    """Return the migration-owner URL pointed at *dbname*."""
    from jai.config import get_settings

    settings = get_settings()
    return URL.create(
        drivername="postgresql+asyncpg",
        username=settings.postgres_migration_user,
        password=settings.postgres_migration_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=dbname,
    ).render_as_string(hide_password=False)


def _get_maintenance_url() -> str:
    """Return a URL pointing at the ``postgres`` maintenance database."""
    from jai.config import get_settings

    settings = get_settings()
    return _url_for_database(settings.database_admin_url, "postgres")


def _get_runtime_test_db_url(dbname: str) -> str:
    """Return the actual runtime-role URL pointed at *dbname*."""
    from jai.config import get_settings

    settings = get_settings()
    return _url_for_database(settings.database_url or "", dbname)


# ---------------------------------------------------------------------------
# Session-scoped: build the template database once.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _migrated_template_db() -> str:
    """Create a template database and run ``alembic upgrade head``.

    Returns the name of the template database.
    """
    maintenance_url = _get_maintenance_url()
    template_url = _get_test_db_url(TEMPLATE_DB_NAME)

    # Connect to maintenance DB to create/drop template.
    engine = create_async_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    import asyncio

    async def _setup() -> None:
        async with engine.begin() as conn:
            # Terminate any lingering connections to the template DB.
            await conn.execute(
                text(
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{TEMPLATE_DB_NAME}' AND pid <> pg_backend_pid()"
                )
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEMPLATE_DB_NAME}"'))
            await conn.execute(text(f'CREATE DATABASE "{TEMPLATE_DB_NAME}"'))
            await conn.execute(
                text(
                    f'ALTER DATABASE "{TEMPLATE_DB_NAME}" OWNER TO '
                    f'"{get_settings().postgres_migration_user}"'
                )
            )
        await engine.dispose()

    asyncio.run(_setup())

    # Run alembic in subprocess (avoids event-loop conflict with asyncio.run).
    migration_env = os.environ.copy()
    migration_env["DATABASE_URL"] = template_url
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=migration_env,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    return TEMPLATE_DB_NAME


# ---------------------------------------------------------------------------
# Per-test: fresh database cloned from the template.
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_engine(_migrated_template_db: str) -> AsyncIterator[AsyncEngine]:
    """Provide a fresh per-test database engine.

    A new database is created from the session template before each test and
    dropped afterwards, ensuring full isolation.
    """
    global _db_counter  # noqa: PLW0603
    _db_counter += 1
    test_db_name = f"jai_test_{_db_counter}"

    maintenance_url = _get_maintenance_url()
    test_db_url = _get_test_db_url(test_db_name)
    template_name = _migrated_template_db

    # Create test database from template.
    maint_engine = create_async_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    async with maint_engine.begin() as conn:
        await conn.execute(
            text(f'CREATE DATABASE "{test_db_name}" TEMPLATE "{template_name}"')
        )
        await conn.execute(
            text(
                f'ALTER DATABASE "{test_db_name}" OWNER TO '
                f'"{get_settings().postgres_migration_user}"'
            )
        )
    await maint_engine.dispose()

    engine = create_async_engine(test_db_url)
    try:
        yield engine
    finally:
        await engine.dispose()
        # Drop test database.
        maint_engine = create_async_engine(maintenance_url, isolation_level="AUTOCOMMIT")
        async with maint_engine.begin() as conn:
            await conn.execute(
                text(
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{test_db_name}' AND pid <> pg_backend_pid()"
                )
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))
        await maint_engine.dispose()


@pytest.fixture
async def db_session_maker(
    db_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Migration-owner factory for direct setup and database assertions.

    It is intentionally not injected into FastAPI; endpoint fixtures use the
    separate runtime-role engine below.
    """
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def runtime_db_engine(db_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """One pooled engine using the real NOSUPERUSER application credentials."""
    database_name = db_engine.url.database
    assert database_name is not None
    engine = create_async_engine(_get_runtime_test_db_url(database_name), pool_size=1)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def admin_session_maker(
    db_engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Maintenance-only superuser factory for verifying FORCE RLS rows.

    Application calls never receive this fixture.  It is intentionally named
    separately from the migration owner and runtime factories to make bypass
    use visible in tests that assert database internals.
    """
    database_name = db_engine.url.database
    assert database_name is not None
    engine = create_async_engine(
        _url_for_database(get_settings().database_admin_url, database_name)
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    finally:
        await engine.dispose()


@pytest.fixture
async def runtime_session_maker(
    runtime_db_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """FastAPI-facing session factory; never a SET ROLE simulation."""
    return async_sessionmaker(runtime_db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def _override_session(
    runtime_session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """Override the app's ``get_session`` dependency for the duration of a test."""

    async def _provider() -> AsyncIterator[AsyncSession]:
        async with runtime_session_maker() as session:
            yield session

    app.dependency_overrides[get_session] = _provider
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """``AsyncClient`` bound to the app — no database dependency.

    Suitable for HTTP-level tests (routing, middleware, static files) that
    don't need a real database session.  For integration tests that need DB
    access through the app, use the ``db_client`` fixture instead.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db_client(_override_session: None) -> AsyncIterator[AsyncClient]:
    """``AsyncClient`` with ``get_session`` pointed at the test DB.

    Only use this for integration tests that exercise DB-backed endpoints.
    Requires a running PostgreSQL instance.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
