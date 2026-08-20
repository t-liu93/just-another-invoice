"""Real-PostgreSQL coverage for M11 Step 3 rate correction."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, cast

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.models.company import Company
from jai.models.expense import Expense
from jai.models.mileage import MileageRateAdjustment, MileageTrip
from jai.schemas.mileage import MileageExpenseWrite, MileageRateWrite
from jai.services import mileage as mileage_service
from jai.services.mileage import seed_for_company


async def _authenticate(client: AsyncClient, email: str) -> None:
    password = "testpassword1"
    assert (
        await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    ).status_code == 201
    assert (
        await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    ).status_code == 200
    setup = await client.post("/api/v1/auth/mfa/setup")
    assert setup.status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/mfa/verify", json={"code": pyotp.TOTP(setup.json()["secret"]).now()}
        )
    ).status_code == 204


async def _setup(client: AsyncClient) -> dict[str, Any]:
    assert (
        await client.put(
            "/api/v1/company",
            json={"name": "Rate correction Co", "base_currency": "EUR", "country_code": "NL"},
        )
    ).status_code == 200
    types = (await client.get("/api/v1/mileage-transport-types")).json()["items"]
    rates = (await client.get("/api/v1/mileage-rates")).json()["items"]
    return {"types": {item["name"]: item for item in types}, "rates": rates}


async def _trip(client: AsyncClient, **extra: object) -> dict[str, Any]:
    body: dict[str, object] = {
        "trip_date": "2026-06-15",
        "one_way_distance_km": "10.000",
        "round_trip": False,
    }
    body.update(extra)
    response = await client.post("/api/v1/mileage-expenses", json=body)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


async def _remove_2026_general_rate(
    client: AsyncClient, setup: dict[str, Any]
) -> dict[str, Any]:
    rate = next(
        item
        for item in setup["rates"]
        if item["effective_from"] == "2026-01-01" and item["transport_type_id"] is None
    )
    assert (await client.delete(f"/api/v1/mileage-rates/{rate['id']}")).status_code == 204
    return cast(dict[str, Any], rate)


async def _wait_for_advisory_lock_waiter(session: AsyncSession, backend_pid: int) -> None:
    """Wait until PostgreSQL proves this backend is blocked on an advisory lock."""
    async with asyncio.timeout(2):
        while True:
            waiting = await session.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_locks "
                    "WHERE pid = :backend_pid AND locktype = 'advisory' "
                    "AND NOT granted)"
                ),
                {"backend_pid": backend_pid},
            )
            if waiting:
                return
            await asyncio.sleep(0)


@pytest.mark.integration
class TestMileageRateRecalculation:
    async def test_preview_apply_history_and_pagination(
        self, db_client: AsyncClient, db_session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        await _authenticate(db_client, "recalculate@example.com")
        setup = await _setup(db_client)
        await _remove_2026_general_rate(db_client, setup)
        first = await _trip(db_client)
        second = await _trip(db_client, one_way_distance_km="20.000")
        added = await db_client.post(
            "/api/v1/mileage-rates",
            json={"effective_from": "2026-01-01", "rate_per_km": "0.250"},
        )
        assert added.status_code == 201

        preview = await db_client.post(
            "/api/v1/mileage-expenses/rate-recalculation/preview?limit=1&offset=1"
        )
        assert preview.status_code == 200, preview.text
        payload = preview.json()
        assert payload["affected_count"] == payload["total"] == 2
        assert payload["limit"] == 1 and payload["offset"] == 1 and len(payload["items"]) == 1
        assert Decimal(payload["old_total"]) == Decimal("6.90")
        assert Decimal(payload["new_total"]) == Decimal("7.50")
        assert Decimal(payload["delta"]) == Decimal("0.60")
        assert "change-me-in-production" not in payload["preview_token"]
        assert all(
            character in "0123456789abcdef_" or character in "mrc"
            for character in payload["preview_token"]
        )

        applied = await db_client.post(
            "/api/v1/mileage-expenses/rate-recalculation/apply",
            json={"preview_token": payload["preview_token"]},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["affected_count"] == 2
        assert Decimal(applied.json()["old_total"]) == Decimal("6.90")
        assert Decimal(applied.json()["new_total"]) == Decimal("7.50")
        assert Decimal(applied.json()["delta"]) == Decimal("0.60")
        history = await db_client.get(f"/api/v1/mileage-expenses/{first['id']}/rate-adjustments")
        assert history.status_code == 200
        item = history.json()["items"][0]
        assert item["trip_id"] == first["id"]
        assert item["old_rate_rule_id"] != item["new_rate_rule_id"]
        assert item["old_rate_transport_type_id"] is item["new_rate_transport_type_id"] is None
        assert item["old_rate_effective_from"] == "2024-01-01"
        assert item["new_rate_effective_from"] == "2026-01-01"
        assert Decimal(item["old_rate_per_km"]) == Decimal("0.230")
        assert Decimal(item["new_rate_per_km"]) == Decimal("0.250")
        assert Decimal(item["old_amount"]) == Decimal("2.30")
        assert Decimal(item["new_amount"]) == Decimal("2.50")

        async with db_session_maker() as session:
            trip = await session.get(MileageTrip, uuid.UUID(str(first["id"])))
            assert trip is not None
            expense = await session.get(Expense, trip.expense_id)
            assert expense is not None
            assert Decimal(str(trip.calculated_amount)) == Decimal("2.50")
            assert Decimal(str(expense.gross_amount)) == Decimal("2.50")
            other_company = Company(name="Other rate correction Co", base_currency="EUR")
            session.add(other_company)
            await session.flush()
            await seed_for_company(session, other_company.id)
            other_preview = await mileage_service.preview_mileage_rate_recalculation(
                session, other_company.id, limit=50, offset=0
            )
            assert other_preview.affected_count == 0
            with pytest.raises(LookupError):
                await mileage_service.list_mileage_rate_adjustments(
                    session, uuid.UUID(str(first["id"])), other_company.id
                )
        fresh = await db_client.post("/api/v1/mileage-expenses/rate-recalculation/preview")
        assert fresh.status_code == 200 and fresh.json()["affected_count"] == 0
        assert (
            await db_client.post(
                "/api/v1/mileage-expenses/rate-recalculation/apply",
                json={"preview_token": fresh.json()["preview_token"]},
            )
        ).json()["affected_count"] == 0

        assert second["id"] != first["id"]

    async def test_override_shields_general_change_and_stale_or_tampered_tokens(
        self, db_client: AsyncClient
    ) -> None:
        await _authenticate(db_client, "override@example.com")
        setup = await _setup(db_client)
        await _remove_2026_general_rate(db_client, setup)
        car = setup["types"]["Car"]
        special = await db_client.post(
            "/api/v1/mileage-transport-types", json={"name": "Shielded", "active": True}
        )
        assert special.status_code == 201
        assert (
            await db_client.post(
                "/api/v1/mileage-rates",
                json={
                    "transport_type_id": special.json()["id"],
                    "effective_from": "2026-01-01",
                    "rate_per_km": "0.300",
                },
            )
        ).status_code == 201
        general_trip = await _trip(db_client, transport_type_id=car["id"])
        await _trip(db_client, transport_type_id=special.json()["id"])
        assert (
            await db_client.post(
                "/api/v1/mileage-rates",
                json={"effective_from": "2026-01-01", "rate_per_km": "0.250"},
            )
        ).status_code == 201
        preview = await db_client.post("/api/v1/mileage-expenses/rate-recalculation/preview")
        assert preview.status_code == 200
        assert preview.json()["affected_count"] == 1
        assert preview.json()["items"][0]["trip_id"] == general_trip["id"]
        token = preview.json()["preview_token"]
        tampered = f"{token[:-1]}{'0' if token[-1] != '0' else '1'}"
        assert (
            await db_client.post(
                "/api/v1/mileage-expenses/rate-recalculation/apply",
                json={"preview_token": tampered},
            )
        ).status_code == 409
        assert (
            await db_client.put(
                f"/api/v1/mileage-expenses/{general_trip['id']}",
                json={
                    "trip_date": "2026-06-15",
                    "transport_type_id": car["id"],
                    "one_way_distance_km": "11.000",
                    "round_trip": False,
                },
            )
        ).status_code == 200
        stale = await db_client.post(
            "/api/v1/mileage-expenses/rate-recalculation/apply", json={"preview_token": token}
        )
        assert stale.status_code == 409

        # A rate edit after a preview is the other concurrent-write path: the
        # old no-op preview may never apply the newly mismatching saved trip.
        zero = await db_client.post("/api/v1/mileage-expenses/rate-recalculation/preview")
        current_general = next(
            item
            for item in (await db_client.get("/api/v1/mileage-rates")).json()["items"]
            if item["effective_from"] == "2026-01-01" and item["transport_type_id"] is None
        )
        assert (
            await db_client.put(
                f"/api/v1/mileage-rates/{current_general['id']}",
                json={"effective_from": "2026-01-01", "rate_per_km": "0.260"},
            )
        ).status_code == 200
        assert (
            await db_client.post(
                "/api/v1/mileage-expenses/rate-recalculation/apply",
                json={"preview_token": zero.json()["preview_token"]},
            )
        ).status_code == 409

    async def test_rate_insert_phantom_waits_then_makes_apply_token_stale(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A real second PostgreSQL session cannot insert behind apply's scan."""
        await _authenticate(db_client, "rate-lock@example.com")
        setup = await _setup(db_client)
        await _remove_2026_general_rate(db_client, setup)
        trip = await _trip(db_client)
        assert (
            await db_client.post(
                "/api/v1/mileage-rates",
                json={"effective_from": "2026-01-01", "rate_per_km": "0.250"},
            )
        ).status_code == 201
        preview = await db_client.post("/api/v1/mileage-expenses/rate-recalculation/preview")
        token = preview.json()["preview_token"]

        async with (
            db_session_maker() as rate_session,
            db_session_maker() as apply_session,
            db_session_maker() as inspector_session,
        ):
            company_id = (
                await inspector_session.scalar(
                    select(Company.id).where(Company.name == "Rate correction Co")
                )
            )
            assert company_id is not None
            apply_pid = await apply_session.scalar(text("SELECT pg_backend_pid()"))
            assert apply_pid is not None

            rate_commit_ready = asyncio.Event()
            release_rate_commit = asyncio.Event()
            original_rate_commit = rate_session.commit

            async def gated_rate_commit() -> None:
                rate_commit_ready.set()
                await release_rate_commit.wait()
                await original_rate_commit()

            monkeypatch.setattr(rate_session, "commit", gated_rate_commit)
            rate_task = asyncio.create_task(
                mileage_service.create_mileage_rate(
                    rate_session,
                    company_id,
                    MileageRateWrite(effective_from=date(2026, 6, 1), rate_per_km=Decimal("0.260")),
                )
            )
            await rate_commit_ready.wait()

            # The writer has acquired the company xact lock but has not yet
            # committed. Apply is a separate session and demonstrably waits.
            apply_task = asyncio.create_task(
                mileage_service.apply_mileage_rate_recalculation(
                    apply_session, company_id, token, actor_id=None
                )
            )
            await _wait_for_advisory_lock_waiter(inspector_session, apply_pid)
            assert not apply_task.done()

            release_rate_commit.set()
            await rate_task
            with pytest.raises(mileage_service.MileageRecalculationPreviewStaleError):
                await apply_task

        async with db_session_maker() as verify_session:
            saved_trip = await verify_session.get(MileageTrip, uuid.UUID(str(trip["id"])))
            assert saved_trip is not None
            projection = await verify_session.get(Expense, saved_trip.expense_id)
            assert projection is not None
            assert Decimal(str(saved_trip.calculated_amount)) == Decimal("2.30")
            assert Decimal(str(projection.gross_amount)) == Decimal("2.30")
            adjustments = await verify_session.execute(
                select(MileageRateAdjustment).where(MileageRateAdjustment.trip_id == saved_trip.id)
            )
            assert adjustments.scalars().all() == []

        current = await db_client.post("/api/v1/mileage-expenses/rate-recalculation/preview")
        assert current.status_code == 200
        assert current.json()["affected_count"] == 1
        assert Decimal(current.json()["items"][0]["new_amount"]) == Decimal("2.60")

    async def test_trip_put_waits_for_apply_and_preserves_adjustment_chain(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PUT locks before its old-snapshot read, preventing a lost update."""
        await _authenticate(db_client, "trip-lock@example.com")
        setup = await _setup(db_client)
        await _remove_2026_general_rate(db_client, setup)
        trip = await _trip(db_client)
        assert (
            await db_client.post(
                "/api/v1/mileage-rates",
                json={"effective_from": "2026-01-01", "rate_per_km": "0.250"},
            )
        ).status_code == 201
        preview = await db_client.post("/api/v1/mileage-expenses/rate-recalculation/preview")
        token = preview.json()["preview_token"]

        async with (
            db_session_maker() as apply_session,
            db_session_maker() as put_session,
            db_session_maker() as inspector_session,
        ):
            company_id = (
                await inspector_session.scalar(
                    select(Company.id).where(Company.name == "Rate correction Co")
                )
            )
            assert company_id is not None
            put_pid = await put_session.scalar(text("SELECT pg_backend_pid()"))
            assert put_pid is not None

            apply_commit_ready = asyncio.Event()
            release_apply_commit = asyncio.Event()
            original_apply_commit = apply_session.commit

            async def gated_apply_commit() -> None:
                apply_commit_ready.set()
                await release_apply_commit.wait()
                await original_apply_commit()

            monkeypatch.setattr(apply_session, "commit", gated_apply_commit)
            apply_task = asyncio.create_task(
                mileage_service.apply_mileage_rate_recalculation(
                    apply_session, company_id, token, actor_id=None
                )
            )
            await apply_commit_ready.wait()

            put_task = asyncio.create_task(
                mileage_service.update_mileage_expense(
                    put_session,
                    uuid.UUID(str(trip["id"])),
                    company_id,
                    MileageExpenseWrite(
                        trip_date=date(2026, 6, 15),
                        one_way_distance_km=Decimal("11.000"),
                        round_trip=False,
                    ),
                    actor_id=None,
                )
            )
            await _wait_for_advisory_lock_waiter(inspector_session, put_pid)
            assert not put_task.done()

            release_apply_commit.set()
            applied = await apply_task
            updated = await put_task
            assert applied.affected_count == 1
            assert updated.rate_per_km == Decimal("0.250")
            assert updated.amount == Decimal("2.75")

        history = await db_client.get(f"/api/v1/mileage-expenses/{trip['id']}/rate-adjustments")
        assert history.status_code == 200
        assert len(history.json()["items"]) == 1
        audit = history.json()["items"][0]
        assert Decimal(audit["old_amount"]) == Decimal("2.30")
        assert Decimal(audit["new_amount"]) == Decimal("2.50")

        async with db_session_maker() as verify_session:
            saved_trip = await verify_session.get(MileageTrip, uuid.UUID(str(trip["id"])))
            assert saved_trip is not None
            projection = await verify_session.get(Expense, saved_trip.expense_id)
            assert projection is not None
            assert Decimal(str(saved_trip.calculated_amount)) == Decimal("2.75")
            assert Decimal(str(projection.gross_amount)) == Decimal("2.75")

    async def test_rollback_does_not_partially_update_or_audit(
        self,
        db_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        await _authenticate(db_client, "rollback@example.com")
        setup = await _setup(db_client)
        await _remove_2026_general_rate(db_client, setup)
        first = await _trip(db_client)
        second = await _trip(db_client)
        assert (
            await db_client.post(
                "/api/v1/mileage-rates",
                json={"effective_from": "2026-01-01", "rate_per_km": "0.250"},
            )
        ).status_code == 201
        preview = await db_client.post("/api/v1/mileage-expenses/rate-recalculation/preview")
        token = preview.json()["preview_token"]
        original = mileage_service._load_locked_projection_expense
        calls = 0

        async def fail_second(
            session: AsyncSession, trip: MileageTrip, company_id: uuid.UUID
        ) -> Expense:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("force rollback")
            return await original(session, trip, company_id)

        monkeypatch.setattr(mileage_service, "_load_locked_projection_expense", fail_second)
        with pytest.raises(RuntimeError, match="force rollback"):
            await db_client.post(
                "/api/v1/mileage-expenses/rate-recalculation/apply",
                json={"preview_token": token},
            )
        # Restore then prove no externally visible rows were changed by the
        # failed transaction.
        monkeypatch.setattr(mileage_service, "_load_locked_projection_expense", original)
        async with db_session_maker() as session:
            rows = list(
                (
                    await session.execute(
                        select(MileageTrip).where(
                            MileageTrip.id.in_(
                                [uuid.UUID(str(first["id"])), uuid.UUID(str(second["id"]))]
                            )
                        )
                    )
                ).scalars()
            )
            assert {Decimal(str(row.calculated_amount)) for row in rows} == {Decimal("2.30")}
            adjustments = await session.execute(
                select(MileageRateAdjustment).where(
                    MileageRateAdjustment.trip_id.in_(
                        [uuid.UUID(str(first["id"])), uuid.UUID(str(second["id"]))]
                    )
                )
            )
            assert adjustments.scalars().all() == []
