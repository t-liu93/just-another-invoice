"""M12 Step 3 Formal Advance API and persistence regression coverage."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_quote_payment_integration import (
    _accept_quote,
    _create_customer,
    _create_quote,
    _full_auth,
    _setup_company,
)

from jai.db import set_rls_company
from jai.models._enums import DocumentChainEventType
from jai.schemas.invoice import InvoiceStatusWrite
from jai.services.advance import AdvanceConflictError, is_advance_draft_conflict
from jai.services.invoice import transition_status

pytestmark = pytest.mark.integration


def _advance_payload(*, percentage: str = "50") -> dict[str, object]:
    return {
        "input_mode": "PERCENTAGE",
        "percentage": percentage,
        "invoice_date": "2026-02-01",
    }


async def _formal_quote(client: AsyncClient) -> tuple[dict, dict]:
    await _full_auth(client)
    seeds = await _setup_company(client)
    return await _additional_formal_quote(client, seeds), seeds


async def _additional_formal_quote(client: AsyncClient, seeds: dict) -> dict:
    customer_id = await _create_customer(client)
    quote = await _create_quote(client, customer_id, seeds["rates"]["NL standard (21%)"]["id"])
    await _accept_quote(client, quote["id"])
    return quote


async def _tail_quote(client: AsyncClient, seeds: dict, *, include_zero_bucket: bool) -> dict:
    """Create a tiny persisted mixed-rate quote whose cents exercise M11.5 tails."""
    customer_id = await _create_customer(client)
    lines: list[dict[str, str]] = [
        {
            "name": "Reduced cent",
            "quantity": "1",
            "unit_price": "0.01",
            "vat_rate_id": seeds["rates"]["NL reduced (9%)"]["id"],
        },
        {
            "name": "Standard tail",
            "quantity": "1",
            "unit_price": "0.04",
            "vat_rate_id": seeds["rates"]["NL standard (21%)"]["id"],
        },
    ]
    if include_zero_bucket:
        lines.insert(
            0,
            {
                "name": "Zero-rate tail",
                "quantity": "1",
                "unit_price": "0.05",
                "vat_rate_id": seeds["rates"]["Zero (0%)"]["id"],
            },
        )
    response = await client.post(
        "/api/v1/quotes",
        json={
            "customer_id": customer_id,
            "quote_date": "2026-01-10",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": lines,
        },
    )
    assert response.status_code == 201, response.text
    quote = response.json()
    await _accept_quote(client, quote["id"])
    return quote


async def test_calculate_create_issue_and_collect_advance(db_client: AsyncClient) -> None:
    quote, _ = await _formal_quote(db_client)
    calculation = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices/calculate",
        json={"input_mode": "PERCENTAGE", "percentage": "50"},
    )
    assert calculation.status_code == 200, calculation.text
    assert calculation.json()["gross_amount"] == "60.50"
    assert Decimal(calculation.json()["taxable_amount"]) + Decimal(
        calculation.json()["vat_total"]
    ) == Decimal("60.50")

    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["document_kind"] == "ADVANCE"
    assert draft["invoice_number"] is None

    locked = await db_client.get(f"/api/v1/quotes/{quote['id']}")
    assert locked.json()["settlement_mode"] == "FORMAL_ADVANCE"
    issued = await db_client.post(f"/api/v1/invoices/{draft['id']}/status", json={"status": "SENT"})
    assert issued.status_code == 200, issued.text
    assert issued.json()["invoice_number"] is not None
    payment = await db_client.post(
        f"/api/v1/invoices/{draft['id']}/payments",
        json={"payment_date": "2026-02-02", "amount": "20.00"},
    )
    assert payment.status_code == 201, payment.text
    assert payment.json()["paid_status"] == "PARTIALLY_PAID"
    paid = await db_client.post(
        f"/api/v1/invoices/{draft['id']}/payments",
        json={"payment_date": "2026-02-03", "amount": "40.50"},
    )
    assert paid.status_code == 201, paid.text
    assert paid.json()["paid_status"] == "PAID"
    overpay = await db_client.post(
        f"/api/v1/invoices/{draft['id']}/payments",
        json={"payment_date": "2026-02-04", "amount": "0.01"},
    )
    assert overpay.status_code == 422
    current = await db_client.get(f"/api/v1/invoices/{draft['id']}")
    assert current.status_code == 200
    assert Decimal(current.json()["due_amount"]) == Decimal("0.00")


async def test_cent_tail_calculate_create_issue_keeps_flat_component_vat_snapshot(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _tail_quote(db_client, seeds, include_zero_bucket=False)
    payload = {
        "input_mode": "GROSS_AMOUNT",
        "gross_amount": "0.04",
        "invoice_date": "2026-02-01",
    }
    calculation = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices/calculate",
        json={"input_mode": "GROSS_AMOUNT", "gross_amount": "0.04"},
    )
    assert calculation.status_code == 200, calculation.text
    assert {
        Decimal(bucket["vat_rate_percent"]): (
            Decimal(bucket["taxable_amount"]),
            Decimal(bucket["vat_amount"]),
        )
        for bucket in calculation.json()["buckets"]
    } == {
        Decimal("9.000"): (Decimal("0.01"), Decimal("0.00")),
        Decimal("21.000"): (Decimal("0.03"), Decimal("0.00")),
    }
    assert calculation.json()["vat_total"] == "0.00"

    created = await db_client.post(f"/api/v1/quotes/{quote['id']}/advance-invoices", json=payload)
    assert created.status_code == 201, created.text
    issued = await db_client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert Decimal(issued.json()["vat_total"]) == Decimal("0.00")
    async with db_session_maker() as session:
        lines = await session.execute(
            text(
                "SELECT vat_rate_percent, taxable_amount, vat_total FROM invoice_line "
                "WHERE invoice_id = :invoice_id ORDER BY vat_rate_percent"
            ),
            {"invoice_id": created.json()["id"]},
        )
        persisted = [
            (Decimal(str(rate)), Decimal(str(net)), Decimal(str(vat)))
            for rate, net, vat in lines
        ]
        assert persisted == [
            (Decimal("9.000"), Decimal("0.010"), Decimal("0.000")),
            (Decimal("21.000"), Decimal("0.030"), Decimal("0.000")),
        ]


async def test_mixed_21_9_0_percentage_advances_close_every_component(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _tail_quote(db_client, seeds, include_zero_bucket=True)
    original_calculation = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices/calculate",
        json={"input_mode": "GROSS_AMOUNT", "gross_amount": "0.11"},
    )
    assert original_calculation.status_code == 200, original_calculation.text
    original = {
        Decimal(bucket["vat_rate_percent"]): (
            Decimal(bucket["taxable_amount"]),
            Decimal(bucket["vat_amount"]),
        )
        for bucket in original_calculation.json()["buckets"]
    }
    allocated: dict[Decimal, list[Decimal]] = {
        rate: [Decimal("0"), Decimal("0")] for rate in original
    }
    for percentage in ("20", "50", "30"):
        created = await db_client.post(
            f"/api/v1/quotes/{quote['id']}/advance-invoices",
            json=_advance_payload(percentage=percentage),
        )
        assert created.status_code == 201, created.text
        issued = await db_client.post(
            f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
        )
        assert issued.status_code == 200, issued.text
        calculation = await db_client.post(
            f"/api/v1/quotes/{quote['id']}/advance-invoices/calculate",
            json={"input_mode": "GROSS_AMOUNT", "gross_amount": "0.01"},
        )
        # The last 30% closes capacity, so its subsequent calculation must not
        # permit even one cent; this guards against negative residual components.
        if percentage == "30":
            assert calculation.status_code == 422
        for bucket in (await db_client.get(f"/api/v1/invoices/{created.json()['id']}")).json()[
            "lines"
        ]:
            rate = Decimal(bucket["vat_rate_percent"])
            allocated[rate][0] += Decimal(bucket["taxable_amount"])
            allocated[rate][1] += Decimal(bucket["vat_total"])
    assert {rate: tuple(amounts) for rate, amounts in allocated.items()} == original


async def test_one_draft_delete_recreate_and_capacity_guard(db_client: AsyncClient) -> None:
    quote, _ = await _formal_quote(db_client)
    first = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    second = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "ADVANCE_CONFLICT"
    assert (await db_client.delete(f"/api/v1/invoices/{first.json()['id']}")).status_code == 204
    recreated = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload(percentage="100")
    )
    assert recreated.status_code == 201, recreated.text
    assert recreated.json()["invoice_number"] is None
    issued = await db_client.post(
        f"/api/v1/invoices/{recreated.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    no_capacity = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert no_capacity.status_code == 422


async def test_document_chain_uses_the_advance_creation_predicate(db_client: AsyncClient) -> None:
    """The authoritative chain action agrees with the Advance create command."""
    quote, _ = await _formal_quote(db_client)

    async def create_advance_available() -> bool:
        chain = await db_client.get(f"/api/v1/quotes/{quote['id']}/document-chain")
        assert chain.status_code == 200, chain.text
        actions = {
            action["code"]: action["available"] for action in chain.json()["available_actions"]
        }
        return actions["CREATE_ADVANCE"]

    assert await create_advance_available()
    draft = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert draft.status_code == 201, draft.text
    assert not await create_advance_available()
    assert (await db_client.delete(f"/api/v1/invoices/{draft.json()['id']}")).status_code == 204
    assert await create_advance_available()

    full = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices",
        json=_advance_payload(percentage="100"),
    )
    assert full.status_code == 201, full.text
    issued = await db_client.post(
        f"/api/v1/invoices/{full.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert not await create_advance_available()


async def test_generic_advance_update_has_a_stable_dedicated_command_error(
    db_client: AsyncClient,
) -> None:
    quote, _ = await _formal_quote(db_client)
    draft = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert draft.status_code == 201, draft.text
    unsupported = await db_client.put(
        f"/api/v1/invoices/{draft.json()['id']}",
        json={
            "customer_id": draft.json()["customer_id"],
            "invoice_date": "2026-02-01",
            "tax_mode": "LINE",
            "lines": [{"name": "ignored by kind guard", "quantity": "1", "unit_price": "1"}],
        },
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"] == {
        "code": "ADVANCE_DEDICATED_UPDATE_REQUIRED",
        "message": "Advance invoices must be updated through the dedicated Advance endpoint.",
    }


async def test_double_create_has_one_draft_and_no_draft_number(db_client: AsyncClient) -> None:
    quote, _ = await _formal_quote(db_client)
    first, second = await asyncio.gather(
        db_client.post(f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()),
        db_client.post(f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()),
    )
    responses = sorted([first, second], key=lambda response: response.status_code)
    assert [response.status_code for response in responses] == [201, 409]
    assert responses[0].json()["invoice_number"] is None


async def test_rounded_zero_and_invalid_due_date_are_rejected_without_a_draft(
    db_client: AsyncClient,
) -> None:
    quote, _ = await _formal_quote(db_client)
    zero = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices/calculate",
        json={"input_mode": "GROSS_AMOUNT", "gross_amount": "0.001"},
    )
    assert zero.status_code == 422
    assert zero.json()["detail"]["code"] == "ADVANCE_AMOUNT_TOO_SMALL"
    invalid_date = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices",
        json={
            "input_mode": "PERCENTAGE",
            "percentage": "20",
            "invoice_date": "2026-02-02",
            "due_date": "2026-02-01",
        },
    )
    assert invalid_date.status_code == 422
    assert invalid_date.json()["detail"]["code"] == "ADVANCE_INVALID_DUE_DATE"
    drafts = await db_client.get("/api/v1/invoices?document_kind=ADVANCE")
    assert drafts.status_code == 200
    assert drafts.json()["total"] == 0


async def test_cancelled_advance_cannot_reopen_over_a_new_draft(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    quote, seeds = await _formal_quote(db_client)
    first = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert first.status_code == 201, first.text
    cancelled = await db_client.post(
        f"/api/v1/invoices/{first.json()['id']}/status", json={"status": "CANCELLED"}
    )
    assert cancelled.status_code == 200, cancelled.text
    second = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert second.status_code == 201, second.text
    reopened = await db_client.post(
        f"/api/v1/invoices/{first.json()['id']}/status", json={"status": "DRAFT"}
    )
    assert reopened.status_code == 409
    assert reopened.json()["detail"]["code"] == "ADVANCE_CONFLICT"

    # The API preflight catches this friendly path, but the PostgreSQL partial
    # unique index remains the authoritative race protection.  Verify its real
    # asyncpg IntegrityError carries the one exact classification we map.
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        with pytest.raises(IntegrityError) as error:
            await session.execute(
                text("UPDATE invoice SET status = 'DRAFT' WHERE id = :invoice_id"),
                {"invoice_id": first.json()["id"]},
            )
        await session.rollback()
    assert is_advance_draft_conflict(error.value)


async def test_lifecycle_preserves_the_real_pg_integrity_error_after_a_flush_failure(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
    runtime_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A trigger-only test proves lifecycle never reads expired ORM kind state.

    The temporary function/trigger is created only in this per-test cloned
    database and is dropped in ``finally``.  It is intentionally not a
    production migration: legal commands do not naturally create this CHECK.
    """
    quote, seeds = await _formal_quote(db_client)
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert created.status_code == 201, created.text
    invoice_id = uuid.UUID(created.json()["id"])
    trigger_name = "tr_m12s3_lifecycle_check"
    function_name = "fn_m12s3_lifecycle_check"

    async with db_session_maker() as session:
        before = (
            await session.execute(
                text(
                    "SELECT status, invoice_number, issued_at, "
                    "(SELECT count(*) FROM invoice_party_snapshot WHERE invoice_id = :id), "
                    "(SELECT count(*) FROM invoice_credit_basis_line WHERE invoice_id = :id), "
                    "(SELECT count(*) FROM document_chain_event "
                    " WHERE invoice_id = :id AND event_type = 'INVOICE_ISSUED'), "
                    "(SELECT count(*) FROM number_sequence WHERE company_id = :company_id) "
                    "FROM invoice WHERE id = :id"
                ),
                {"id": invoice_id, "company_id": seeds["company_id"]},
            )
        ).one()
        await session.execute(
            text(
                f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN "
                f"IF NEW.id = '{invoice_id}'::uuid AND NEW.status = 'SENT' THEN "
                "RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'ck_m12s3_lifecycle_check'; "
                "END IF; RETURN NEW; END; $$"
            )
        )
        await session.execute(
            text(
                f"CREATE TRIGGER {trigger_name} BEFORE UPDATE ON invoice "
                f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
            )
        )
        await session.commit()

    try:
        async with runtime_session_maker() as session:
            with pytest.raises(IntegrityError) as error:
                await transition_status(
                    session,
                    invoice_id,
                    seeds["company_id"],
                    InvoiceStatusWrite(status="SENT"),
                )
        assert not is_advance_draft_conflict(error.value)
    finally:
        async with db_session_maker() as session:
            await session.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name} ON invoice"))
            await session.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))
            await session.commit()

    async with db_session_maker() as session:
        after = (
            await session.execute(
                text(
                    "SELECT status, invoice_number, issued_at, "
                    "(SELECT count(*) FROM invoice_party_snapshot WHERE invoice_id = :id), "
                    "(SELECT count(*) FROM invoice_credit_basis_line WHERE invoice_id = :id), "
                    "(SELECT count(*) FROM document_chain_event "
                    " WHERE invoice_id = :id AND event_type = 'INVOICE_ISSUED'), "
                    "(SELECT count(*) FROM number_sequence WHERE company_id = :company_id) "
                    "FROM invoice WHERE id = :id"
                ),
                {"id": invoice_id, "company_id": seeds["company_id"]},
            )
        ).one()
    assert before == after
    assert after[:6] == ("DRAFT", None, None, 0, 0, 0)


async def test_lifecycle_maps_only_the_real_partial_unique_reopen_conflict(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
    runtime_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A temporary trigger makes the real partial index fire after preflight."""
    quote, seeds = await _formal_quote(db_client)
    first = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert first.status_code == 201, first.text
    assert (
        await db_client.post(
            f"/api/v1/invoices/{first.json()['id']}/status", json={"status": "CANCELLED"}
        )
    ).status_code == 200
    second = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert second.status_code == 201, second.text
    assert (
        await db_client.post(
            f"/api/v1/invoices/{second.json()['id']}/status", json={"status": "CANCELLED"}
        )
    ).status_code == 200

    trigger_name = "tr_m12s3_lifecycle_partial"
    function_name = "fn_m12s3_lifecycle_partial"
    async with db_session_maker() as session:
        await session.execute(
            text(
                f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN "
                f"IF NEW.id = '{first.json()['id']}'::uuid AND NEW.status = 'DRAFT' THEN "
                f"UPDATE invoice SET status = 'DRAFT' WHERE id = '{second.json()['id']}'::uuid; "
                "END IF; RETURN NEW; END; $$"
            )
        )
        await session.execute(
            text(
                f"CREATE TRIGGER {trigger_name} BEFORE UPDATE ON invoice "
                f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
            )
        )
        await session.commit()
    try:
        async with runtime_session_maker() as session:
            with pytest.raises(AdvanceConflictError) as error:
                await transition_status(
                    session,
                    uuid.UUID(first.json()["id"]),
                    seeds["company_id"],
                    InvoiceStatusWrite(status="DRAFT"),
                )
        assert error.value.code == "ADVANCE_CONFLICT"
    finally:
        async with db_session_maker() as session:
            await session.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name} ON invoice"))
            await session.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))
            await session.commit()


@pytest.mark.parametrize("sqlstate", ["23503", "23514"])
async def test_lifecycle_keeps_unrelated_real_pg_reopen_errors_raw(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
    runtime_session_maker: async_sessionmaker[AsyncSession],
    sqlstate: str,
) -> None:
    quote, seeds = await _formal_quote(db_client)
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert created.status_code == 201, created.text
    invoice_id = uuid.UUID(created.json()["id"])
    assert (
        await db_client.post(f"/api/v1/invoices/{invoice_id}/status", json={"status": "CANCELLED"})
    ).status_code == 200
    trigger_name = f"tr_m12s3_lifecycle_{sqlstate}"
    function_name = f"fn_m12s3_lifecycle_{sqlstate}"
    async with db_session_maker() as session:
        await session.execute(
            text(
                f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN "
                f"IF NEW.id = '{invoice_id}'::uuid AND NEW.status = 'DRAFT' THEN "
                f"RAISE EXCEPTION USING ERRCODE = '{sqlstate}', "
                f"CONSTRAINT = 'm12s3_unrelated_{sqlstate}'; "
                "END IF; RETURN NEW; END; $$"
            )
        )
        await session.execute(
            text(
                f"CREATE TRIGGER {trigger_name} BEFORE UPDATE ON invoice "
                f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
            )
        )
        await session.commit()
    try:
        async with runtime_session_maker() as session:
            with pytest.raises(IntegrityError) as error:
                await transition_status(
                    session,
                    invoice_id,
                    seeds["company_id"],
                    InvoiceStatusWrite(status="DRAFT"),
                )
        assert not is_advance_draft_conflict(error.value)
    finally:
        async with db_session_maker() as session:
            await session.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name} ON invoice"))
            await session.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))
            await session.commit()


async def test_lifecycle_number_unique_keeps_its_existing_error_branch(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
    runtime_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    quote, seeds = await _formal_quote(db_client)
    standard = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": quote["customer_id"],
            "invoice_date": "2026-02-01",
            "tax_mode": "LINE",
            "lines": [
                {
                    "name": "Number conflict source",
                    "quantity": "1",
                    "unit_price": "1",
                    "vat_rate_id": seeds["rates"]["NL standard (21%)"]["id"],
                }
            ],
        },
    )
    assert standard.status_code == 201, standard.text
    assert (
        await db_client.post(
            f"/api/v1/invoices/{standard.json()['id']}/status", json={"status": "SENT"}
        )
    ).status_code == 200
    advance = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert advance.status_code == 201, advance.text
    trigger_name = "tr_m12s3_lifecycle_number"
    function_name = "fn_m12s3_lifecycle_number"
    async with db_session_maker() as session:
        before_next = await session.scalar(
            text(
                "SELECT next_value FROM number_sequence "
                "WHERE company_id = :company_id AND document_type = 'INVOICE' "
                "AND scope = 'COMPANY'"
            ),
            {"company_id": seeds["company_id"]},
        )
        await session.execute(
            text(
                f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN "
                f"IF NEW.id = '{advance.json()['id']}'::uuid AND NEW.status = 'SENT' THEN "
                "UPDATE invoice SET invoice_number = NEW.invoice_number "
                f"WHERE id = '{standard.json()['id']}'::uuid; "
                "END IF; RETURN NEW; END; $$"
            )
        )
        await session.execute(
            text(
                f"CREATE TRIGGER {trigger_name} BEFORE UPDATE ON invoice "
                f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
            )
        )
        await session.commit()
    try:
        async with runtime_session_maker() as session:
            with pytest.raises(ValueError, match="Invoice number already exists"):
                await transition_status(
                    session,
                    uuid.UUID(advance.json()["id"]),
                    seeds["company_id"],
                    InvoiceStatusWrite(status="SENT"),
                )
    finally:
        async with db_session_maker() as session:
            await session.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name} ON invoice"))
            await session.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))
            await session.commit()
    async with db_session_maker() as session:
        state = (
            await session.execute(
                text(
                    "SELECT status, invoice_number, "
                    "(SELECT next_value FROM number_sequence "
                    " WHERE company_id = :company_id AND document_type = 'INVOICE' "
                    " AND scope = 'COMPANY') "
                    ",(SELECT count(*) FROM invoice_party_snapshot WHERE invoice_id = :invoice_id) "
                    ",(SELECT count(*) FROM invoice_credit_basis_line "
                    " WHERE invoice_id = :invoice_id) "
                    ",(SELECT count(*) FROM document_chain_event "
                    " WHERE invoice_id = :invoice_id AND event_type = 'INVOICE_ISSUED') "
                    "FROM invoice WHERE id = :invoice_id"
                ),
                {"company_id": seeds["company_id"], "invoice_id": advance.json()["id"]},
            )
        ).one()
    assert state == ("DRAFT", None, before_next, 0, 0, 0)


async def test_advance_delete_races_keep_quote_before_invoice_lock_order(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete/create, delete/issue and delete/reopen never deadlock or leave debris.

    The event hook pauses DELETE after it acquired all service locks but before
    it writes the deletion event.  The old Invoice -> Quote delete order then
    formed a real PostgreSQL deadlock with each of these Quote -> Invoice
    actions.  Repeat every pair to make scheduling regressions reproducible.
    """
    from jai.api import invoices as invoice_api
    from jai.services import document_chain

    quote, _ = await _formal_quote(db_client)
    current_draft: dict | None = None
    pause: dict[str, object] = {}
    original_append = document_chain.append_document_chain_event
    original_create = invoice_api.create_advance_draft
    original_transition = invoice_api.transition_status

    async def pause_delete_event(*args: object, **kwargs: object) -> object:
        if (
            kwargs.get("event_type") == DocumentChainEventType.INVOICE_DELETED
            and str(kwargs.get("invoice_id")) == pause.get("invoice_id")
        ):
            entered = pause["entered"]
            release = pause["release"]
            assert isinstance(entered, asyncio.Event)
            assert isinstance(release, asyncio.Event)
            entered.set()
            await release.wait()
        return await original_append(*args, **kwargs)

    monkeypatch.setattr(document_chain, "append_document_chain_event", pause_delete_event)

    async def mark_create_started(*args: object, **kwargs: object) -> object:
        started = pause.get("mutation_started")
        if isinstance(started, asyncio.Event):
            started.set()
        return await original_create(*args, **kwargs)

    async def mark_transition_started(*args: object, **kwargs: object) -> object:
        started = pause.get("mutation_started")
        if isinstance(started, asyncio.Event):
            started.set()
        return await original_transition(*args, **kwargs)

    monkeypatch.setattr(invoice_api, "create_advance_draft", mark_create_started)
    monkeypatch.setattr(invoice_api, "transition_status", mark_transition_started)

    async def assert_deleted_without_debris(invoice_id: str) -> None:
        async with db_session_maker() as session:
            counts = (
                await session.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM invoice WHERE id = :id), "
                        "(SELECT count(*) FROM invoice_line WHERE invoice_id = :id), "
                        "(SELECT count(*) FROM document_chain_event WHERE invoice_id = :id)"
                    ),
                    {"id": invoice_id},
                )
            ).one()
        assert counts == (0, 0, 0)

    async def run_race(kind: str) -> None:
        nonlocal current_draft
        cancelled: dict | None = None
        if kind == "reopen":
            original = await db_client.post(
                f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
            )
            assert original.status_code == 201, original.text
            deleted_draft = original.json()
            cancelled_response = await db_client.post(
                f"/api/v1/invoices/{deleted_draft['id']}/status", json={"status": "CANCELLED"}
            )
            assert cancelled_response.status_code == 200, cancelled_response.text
            cancelled = cancelled_response.json()
            replacement = await db_client.post(
                f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
            )
            assert replacement.status_code == 201, replacement.text
            deleted_draft = replacement.json()
        else:
            if current_draft is None:
                original = await db_client.post(
                    f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
                )
                assert original.status_code == 201, original.text
                current_draft = original.json()
            deleted_draft = current_draft

        entered = asyncio.Event()
        release = asyncio.Event()
        mutation_started = asyncio.Event()
        pause.clear()
        pause.update(
            invoice_id=deleted_draft["id"],
            entered=entered,
            release=release,
            mutation_started=mutation_started,
        )
        delete_task = asyncio.create_task(
            db_client.delete(f"/api/v1/invoices/{deleted_draft['id']}")
        )
        mutation_task: asyncio.Task[object] | None = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            if kind == "create":
                mutation_task = asyncio.create_task(
                    db_client.post(
                        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
                    )
                )
            elif kind == "issue":
                mutation_task = asyncio.create_task(
                    db_client.post(
                        f"/api/v1/invoices/{deleted_draft['id']}/status", json={"status": "SENT"}
                    )
                )
            else:
                assert cancelled is not None
                mutation_task = asyncio.create_task(
                    db_client.post(
                        f"/api/v1/invoices/{cancelled['id']}/status", json={"status": "DRAFT"}
                    )
                )
            await asyncio.wait_for(mutation_started.wait(), timeout=5)
            assert not mutation_task.done()
            release.set()
            deleted, mutation = await asyncio.wait_for(
                asyncio.gather(delete_task, mutation_task), timeout=10
            )
            assert deleted.status_code == 204, deleted.text
            assert mutation.status_code != 500, mutation.text
            assert mutation.status_code == {"create": 201, "issue": 404, "reopen": 200}[kind]
            await assert_deleted_without_debris(deleted_draft["id"])
            if kind == "create":
                assert mutation.json()["invoice_number"] is None
                current_draft = mutation.json()
            elif kind == "issue":
                current_draft = None
            if kind == "reopen":
                assert mutation.json()["invoice_number"] is None
                current_draft = mutation.json()
        finally:
            release.set()
            pending = [delete_task]
            if mutation_task is not None:
                pending.append(mutation_task)
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    for _ in range(3):
        await run_race("create")
        await run_race("issue")
        await run_race("reopen")


async def test_advance_intent_round_trips_without_precision_loss(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """The persisted command is exactly what the issue-time allocator replays."""
    gross_quote, seeds = await _formal_quote(db_client)
    gross_created = await db_client.post(
        f"/api/v1/quotes/{gross_quote['id']}/advance-invoices",
        json={
            "input_mode": "GROSS_AMOUNT",
            "gross_amount": "1.2349",
            "invoice_date": "2026-02-01",
        },
    )
    assert gross_created.status_code == 201, gross_created.text
    gross_draft = gross_created.json()
    assert gross_draft["total_incl_vat"] == "1.23"
    async with db_session_maker() as session:
        stored = await session.execute(
            text("SELECT advance_gross_amount FROM invoice WHERE id = :id"),
            {"id": gross_draft["id"]},
        )
        assert Decimal(str(stored.scalar_one())) == Decimal("1.230")
    gross_issued = await db_client.post(
        f"/api/v1/invoices/{gross_draft['id']}/status", json={"status": "SENT"}
    )
    assert gross_issued.status_code == 200, gross_issued.text
    assert Decimal(gross_issued.json()["total_incl_vat"]) == Decimal("1.23")

    percentage_quote = await _additional_formal_quote(db_client, seeds)
    percentage_created = await db_client.post(
        f"/api/v1/quotes/{percentage_quote['id']}/advance-invoices",
        json=_advance_payload(percentage="3.333"),
    )
    assert percentage_created.status_code == 201, percentage_created.text
    percentage_draft = percentage_created.json()
    updated = await db_client.put(
        f"/api/v1/advance-invoices/{percentage_draft['id']}",
        json=_advance_payload(percentage="3.333"),
    )
    assert updated.status_code == 200, updated.text
    async with db_session_maker() as session:
        stored = await session.execute(
            text("SELECT advance_percentage FROM invoice WHERE id = :id"),
            {"id": percentage_draft["id"]},
        )
        assert Decimal(str(stored.scalar_one())) == Decimal("3.333")
    percentage_issued = await db_client.post(
        f"/api/v1/invoices/{percentage_draft['id']}/status", json={"status": "SENT"}
    )
    assert percentage_issued.status_code == 200, percentage_issued.text
    assert Decimal(percentage_issued.json()["total_incl_vat"]) == Decimal(
        updated.json()["total_incl_vat"]
    )

    rejected_quote = await _additional_formal_quote(db_client, seeds)
    rejected = await db_client.post(
        f"/api/v1/quotes/{rejected_quote['id']}/advance-invoices",
        json=_advance_payload(percentage="3.3333"),
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "ADVANCE_PERCENTAGE_PRECISION"
    invoices = await db_client.get("/api/v1/invoices?document_kind=ADVANCE")
    assert invoices.status_code == 200
    # Only the two successfully created/issued documents exist: the rejected
    # command cannot lock a quote mode or leave a Draft/event behind.
    assert invoices.json()["total"] == 2
    rejected_quote_read = await db_client.get(f"/api/v1/quotes/{rejected_quote['id']}")
    assert rejected_quote_read.status_code == 200
    assert rejected_quote_read.json()["settlement_mode"] == "UNSET"


async def test_advance_is_excluded_from_legacy_reports_until_step_8(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Step-3 compatibility gate: old Standard projections never infer formal events."""
    quote, _ = await _formal_quote(db_client)
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload()
    )
    assert created.status_code == 201, created.text
    invoice_id = created.json()["id"]
    issued = await db_client.post(f"/api/v1/invoices/{invoice_id}/status", json={"status": "SENT"})
    assert issued.status_code == 200, issued.text
    payment = await db_client.post(
        f"/api/v1/invoices/{invoice_id}/payments",
        json={"payment_date": "2026-02-02", "amount": "60.50"},
    )
    assert payment.status_code == 201, payment.text

    profit_loss = await db_client.get(
        "/api/v1/reports/profit-loss?from=2026-01-01&to=2026-12-31"
    )
    assert profit_loss.status_code == 200, profit_loss.text
    assert profit_loss.json()["revenue_net"] == "0"
    assert profit_loss.json()["profit"] == "0"
    dashboard = await db_client.get("/api/v1/reports/dashboard?year=2026")
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["kpi"]["ytd_revenue"] == "0"
    assert dashboard.json()["kpi"]["ytd_profit"] == "0"
    vat = await db_client.get("/api/v1/reports/vat-return?year=2026&quarter=1")
    assert vat.status_code == 200, vat.text
    assert vat.json()["boxes"]["box_1a"]["base"] == "0"
    assert vat.json()["boxes"]["box_1a"]["vat"] == "0"

    # Formal Advances are NL_DOMESTIC and therefore normally cannot appear in
    # ICP. Set the legacy snapshot flag directly only to prove ICP applies the
    # same kind gate; this is not a Step-8 tax-event implementation.
    async with db_session_maker() as session:
        await session.execute(
            text(
                "UPDATE invoice SET vat_treatment_requires_icp = TRUE "
                "WHERE id = :id"
            ),
            {"id": invoice_id},
        )
        await session.commit()
    icp = await db_client.get("/api/v1/reports/icp?year=2026&quarter=1")
    assert icp.status_code == 200, icp.text
    assert icp.json()["total_net"] == "0"
    assert icp.json()["lines"] == []
