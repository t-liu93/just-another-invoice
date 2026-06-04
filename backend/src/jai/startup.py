"""Startup checks – fail-fast if the database is not at the expected state.

Currently checks that the Alembic migration head matches the latest
revision known to the codebase.  In production, the migration service
runs ``alembic upgrade head`` before the app starts; if the DB is not
at head by the time the app boots, something is wrong and we refuse to
start (fail-closed).
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("jai.startup")

#: Absolute path to the ``backend/`` directory (where alembic.ini lives).
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


async def check_db_migration(engine: AsyncEngine) -> None:
    """Verify that the database is at the latest Alembic head.

    Raises ``SystemExit`` if the database revision does not match the
    expected head revision.  This is a **fail-closed** check: the
    application should not serve requests if the schema is stale.
    """
    # Resolve the expected head from the local Alembic scripts.
    alembic_ini = _BACKEND_DIR / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(alembic_cfg)
    expected_head = script.get_current_head()

    if expected_head is None:
        logger.warning("No Alembic revisions found – skipping migration check.")
        return

    # Read the actual revision from the database.
    async with engine.connect() as conn:
        # Check if alembic_version table exists.
        result = await conn.execute(text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables"
            "  WHERE table_name = 'alembic_version'"
            ")"
        ))
        table_exists = result.scalar()

        if not table_exists:
            logger.critical(
                "Database has no 'alembic_version' table. "
                "Run 'alembic upgrade head' before starting the application."
            )
            raise SystemExit(1)

        result = await conn.execute(text(
            "SELECT version_num FROM alembic_version"
        ))
        row = result.scalar_one_or_none()

    if row is None:
        logger.critical(
            "Database 'alembic_version' table is empty. "
            "Run 'alembic upgrade head' before starting the application."
        )
        raise SystemExit(1)

    actual = row
    if actual != expected_head:
        logger.critical(
            "Database migration mismatch: "
            "actual=%s, expected=%s. "
            "Run 'alembic upgrade head' before starting the application.",
            actual,
            expected_head,
        )
        raise SystemExit(1)

    logger.info("Database migration check passed (revision=%s).", actual)
