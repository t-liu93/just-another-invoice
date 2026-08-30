"""Real PostgreSQL race coverage for the M12 Refund tombstone downgrade."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from test_m12_artifact_integration import _all_formal_documents
from test_migrations import _run_alembic

from jai.db import set_rls_company
from jai.models.payment import Payment

pytestmark = pytest.mark.integration


async def test_0040_downgrade_lock_blocks_live_refund_delete_without_resurrection(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A downgrade holds payment's check lock before checking tombstones."""
    documents, seeds = await _all_formal_documents(db_client)
    refund_id = documents["REFUND"]["id"]
    bind = db_session_maker.kw["bind"]
    assert isinstance(bind, AsyncEngine)
    assert bind.url.database not in {None, "jai_test_template"}
    migration_url = bind.url.render_as_string(hide_password=False)
    blocker = create_async_engine(migration_url, pool_size=1, max_overflow=0)
    try:
        async with blocker.connect() as conn:
            transaction = await conn.begin()
            # Blocks ALTER's ACCESS EXCLUSIVE but permits the migration's
            # SHARE ROW EXCLUSIVE check lock, making the interleaving stable.
            await conn.execute(text("LOCK TABLE payment IN ACCESS SHARE MODE"))
            downgrade_task = asyncio.create_task(
                asyncio.to_thread(_run_alembic, "downgrade", "0039", url=migration_url)
            )
            for _ in range(100):
                has_check_lock = await conn.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks l "
                        "JOIN pg_class c ON c.oid = l.relation "
                        "WHERE c.relname = 'payment' AND l.granted "
                        "AND l.mode = 'ShareRowExclusiveLock')"
                    )
                )
                if has_check_lock:
                    break
                await asyncio.sleep(0.05)
            assert has_check_lock, "downgrade never acquired its tombstone check lock"
            delete_task = asyncio.create_task(db_client.delete(f"/api/v1/payments/{refund_id}"))
            await asyncio.sleep(0.2)
            assert not delete_task.done(), "live Refund DELETE bypassed downgrade table lock"
            await transaction.commit()
            downgrade = await asyncio.wait_for(downgrade_task, timeout=30)
            assert downgrade.returncode == 0, downgrade.stderr
            delete_result = await asyncio.gather(delete_task, return_exceptions=True)
    finally:
        await blocker.dispose()

    # After 0039 has removed deleted_at, the queued runtime DELETE cannot
    # apply a tombstone and fails instead of reviving/altering cash history.
    assert isinstance(delete_result[0], Exception)
    upgrade = _run_alembic("upgrade", "0040", url=migration_url)
    assert upgrade.returncode == 0, upgrade.stderr
    async with db_session_maker() as session:
        await set_rls_company(session, uuid.UUID(seeds["company_id"]))
        refund = await session.get(Payment, uuid.UUID(refund_id))
        assert refund is not None and refund.deleted_at is None
