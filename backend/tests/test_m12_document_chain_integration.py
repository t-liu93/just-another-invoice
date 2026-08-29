"""PostgreSQL/runtime integration coverage for M12 Step 2 chain invariants."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from test_quote_payment_integration import (
    _accept_quote,
    _create_customer,
    _create_invoice,
    _create_quote,
    _full_auth,
    _record,
    _setup_company,
)

from jai.db import set_rls_company
from jai.models.quote import Quote
from jai.services.document_chain import get_document_chain

pytestmark = pytest.mark.integration


def _actions(payload: dict[str, object]) -> dict[str, bool]:
    return {
        row["code"]: row["available"]  # type: ignore[index]
        for row in payload["available_actions"]  # type: ignore[index]
    }


async def test_receipt_only_conversion_uses_provenance_not_backlink(
    db_client: AsyncClient,
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    quote = await _create_quote(db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"])
    await _accept_quote(db_client, quote["id"])
    await _record(db_client, quote["id"], "10", "2026-01-15")

    chain = await db_client.get(f"/api/v1/quotes/{quote['id']}/document-chain")
    assert chain.status_code == 200, chain.text
    assert _actions(chain.json())["CONVERT_TO_INVOICE"] is True
    assert [event["event_type"] for event in chain.json()["events"]][:2] == [
        "MODE_LOCKED",
        "QUOTE_PAYMENT_CREATED",
    ]

    converted = await db_client.post(f"/api/v1/quotes/{quote['id']}/convert")
    assert converted.status_code == 201, converted.text
    async with admin_session_maker() as session:
        await session.execute(
            update(Quote).where(Quote.id == quote["id"]).values(converted_invoice_id=None)
        )
        await session.commit()

    chain = await db_client.get(f"/api/v1/quotes/{quote['id']}/document-chain")
    assert _actions(chain.json())["CONVERT_TO_INVOICE"] is False
    assert any(edge["relation_type"] == "QUOTE_TO_INVOICE" for edge in chain.json()["relations"])
    converted_payment = next(
        node for node in chain.json()["nodes"] if node["node_type"] == "PAYMENT"
    )
    payment_edges = [
        edge for edge in chain.json()["relations"] if edge["to_node_id"] == converted_payment["id"]
    ]
    assert {edge["relation_type"] for edge in payment_edges} == {
        "QUOTE_TO_PAYMENT",
        "INVOICE_TO_PAYMENT",
    }
    again = await db_client.post(f"/api/v1/quotes/{quote['id']}/convert")
    assert again.status_code == 409


async def test_standalone_root_payment_events_and_runtime_append_only(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    invoice = await _create_invoice(
        db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"]
    )
    payment = await db_client.post(
        f"/api/v1/invoices/{invoice['id']}/payments",
        json={"payment_date": "2026-01-15", "amount": "10"},
    )
    assert payment.status_code == 201, payment.text
    chain = await db_client.get(f"/api/v1/invoices/{invoice['id']}/document-chain")
    assert chain.status_code == 200, chain.text
    assert chain.json()["nodes"][0]["id"] == invoice["id"]
    assert {event["event_type"] for event in chain.json()["events"]} >= {
        "INVOICE_CREATED",
        "INVOICE_ISSUED",
        "INVOICE_PAYMENT_CREATED",
    }

    async with runtime_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        with pytest.raises(DBAPIError):
            await session.execute(
                text("UPDATE document_chain_event SET metadata_json = '{}'::jsonb")
            )
        await session.rollback()
        await set_rls_company(session, seeds["company_id"])
        with pytest.raises(DBAPIError):
            await session.execute(text("DELETE FROM document_chain_event"))
        await session.rollback()


async def test_mode_conflicts_failed_first_action_and_multiple_payments(
    db_client: AsyncClient,
) -> None:
    """Mode lock survives failures/deletes and old branches cannot mix."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    rate_id = seeds["rates"]["NL standard (21%)"]["id"]

    failed = await _create_quote(db_client, customer_id, rate_id)
    await _accept_quote(db_client, failed["id"])
    rejected = await db_client.post(
        f"/api/v1/quotes/{failed['id']}/payments",
        json={"payment_date": "2026-01-15", "amount": "999999"},
    )
    assert rejected.status_code == 422
    after_failure = await db_client.get(f"/api/v1/quotes/{failed['id']}/document-chain")
    assert after_failure.json()["settlement_mode"] == "UNSET"
    assert after_failure.json()["events"] == []

    receipt = await _create_quote(db_client, customer_id, rate_id)
    await _accept_quote(db_client, receipt["id"])
    first = await _record(db_client, receipt["id"], "10", "2026-01-15")
    second = await _record(db_client, receipt["id"], "10", "2026-01-16")
    payment_id = first["items"][0]["id"]
    second_payment_id = next(item["id"] for item in second["items"] if item["id"] != payment_id)
    assert (await db_client.delete(f"/api/v1/payments/{payment_id}")).status_code == 200
    chain = await db_client.get(f"/api/v1/quotes/{receipt['id']}/document-chain")
    assert chain.json()["settlement_mode"] == "RECEIPT_ONLY"
    assert len([node for node in chain.json()["nodes"] if node["node_type"] == "PAYMENT"]) == 1
    assert any(edge["relation_type"] == "QUOTE_TO_PAYMENT" for edge in chain.json()["relations"])
    assert second_payment_id in {node["id"] for node in chain.json()["nodes"]}

    direct = await _create_quote(db_client, customer_id, rate_id)
    await _accept_quote(db_client, direct["id"])
    converted = await db_client.post(f"/api/v1/quotes/{direct['id']}/convert")
    assert converted.status_code == 201, converted.text
    cross_mode = await db_client.post(
        f"/api/v1/quotes/{direct['id']}/payments",
        json={"payment_date": "2026-01-15", "amount": "10"},
    )
    assert cross_mode.status_code == 409


async def test_quote_linked_invoice_payment_events_are_projected_and_audited(
    db_client: AsyncClient,
) -> None:
    """Both Quote and Invoice GETs include invoice-cash lifecycle events."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    quote = await _create_quote(db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"])
    await _accept_quote(db_client, quote["id"])
    converted = await db_client.post(f"/api/v1/quotes/{quote['id']}/convert")
    assert converted.status_code == 201, converted.text
    invoice_id = converted.json()["id"]
    issued = await db_client.post(f"/api/v1/invoices/{invoice_id}/status", json={"status": "SENT"})
    assert issued.status_code == 200, issued.text
    created = await db_client.post(
        f"/api/v1/invoices/{invoice_id}/payments",
        json={"payment_date": "2026-01-15", "amount": "10"},
    )
    assert created.status_code == 201, created.text
    payment_id = created.json()["items"][0]["id"]
    updated = await db_client.put(
        f"/api/v1/payments/{payment_id}",
        json={"payment_date": "2026-01-16", "amount": "11"},
    )
    assert updated.status_code == 200, updated.text
    deleted = await db_client.delete(f"/api/v1/payments/{payment_id}")
    assert deleted.status_code == 200, deleted.text

    quote_chain = await db_client.get(f"/api/v1/quotes/{quote['id']}/document-chain")
    invoice_chain = await db_client.get(f"/api/v1/invoices/{invoice_id}/document-chain")
    assert quote_chain.status_code == invoice_chain.status_code == 200
    expected = {
        "INVOICE_PAYMENT_CREATED",
        "INVOICE_PAYMENT_UPDATED",
        "INVOICE_PAYMENT_DELETED",
    }
    for payload in (quote_chain.json(), invoice_chain.json()):
        events = payload["events"]
        assert expected <= {event["event_type"] for event in events}
        event_ids = [event["id"] for event in events]
        assert event_ids == list(dict.fromkeys(event_ids))
        assert all(event["actor_user_id"] is not None for event in events)


async def test_formal_and_existing_conversion_conflicts_are_machine_readable(
    db_client: AsyncClient,
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    rate_id = seeds["rates"]["NL standard (21%)"]["id"]
    formal = await _create_quote(db_client, customer_id, rate_id)
    await _accept_quote(db_client, formal["id"])
    async with admin_session_maker() as session:
        await session.execute(
            update(Quote).where(Quote.id == formal["id"]).values(settlement_mode="FORMAL_ADVANCE")
        )
        await session.commit()
    formal_convert = await db_client.post(f"/api/v1/quotes/{formal['id']}/convert")
    assert formal_convert.status_code == 409
    assert formal_convert.json()["detail"]["code"] == "MODE_CONFLICT"

    direct = await _create_quote(db_client, customer_id, rate_id)
    await _accept_quote(db_client, direct["id"])
    assert (await db_client.post(f"/api/v1/quotes/{direct['id']}/convert")).status_code == 201
    again = await db_client.post(f"/api/v1/quotes/{direct['id']}/convert")
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "ALREADY_CONVERTED"


async def test_four_mode_action_matrix_and_cross_company_service_read(
    db_client: AsyncClient,
    admin_session_maker: async_sessionmaker[AsyncSession],
    runtime_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Action predicates and commands agree for every persisted Quote mode."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    rate_id = seeds["rates"]["NL standard (21%)"]["id"]
    quotes = [await _create_quote(db_client, customer_id, rate_id) for _ in range(4)]
    for quote in quotes:
        await _accept_quote(db_client, quote["id"])
    unset, direct, receipt, formal = quotes

    assert _actions(
        (await db_client.get(f"/api/v1/quotes/{unset['id']}/document-chain")).json()
    )["CONVERT_TO_INVOICE"]
    assert (await db_client.post(f"/api/v1/quotes/{direct['id']}/convert")).status_code == 201
    assert (await db_client.post(
        f"/api/v1/quotes/{receipt['id']}/payments",
        json={"payment_date": "2026-01-15", "amount": "10"},
    )).status_code == 201
    async with admin_session_maker() as session:
        await session.execute(
            update(Quote).where(Quote.id == formal["id"]).values(settlement_mode="FORMAL_ADVANCE")
        )
        await session.commit()

    direct_chain = (await db_client.get(f"/api/v1/quotes/{direct['id']}/document-chain")).json()
    receipt_chain = (await db_client.get(f"/api/v1/quotes/{receipt['id']}/document-chain")).json()
    formal_chain = (await db_client.get(f"/api/v1/quotes/{formal['id']}/document-chain")).json()
    assert not _actions(direct_chain)["RECORD_QUOTE_PAYMENT"]
    assert _actions(receipt_chain)["CONVERT_TO_INVOICE"]
    assert not _actions(formal_chain)["CONVERT_TO_INVOICE"]
    conflict = await db_client.post(f"/api/v1/quotes/{formal['id']}/convert")
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "MODE_CONFLICT"

    async with runtime_session_maker() as session:
        assert await get_document_chain(
            session, company_id=uuid.uuid4(), quote_id=uuid.UUID(unset["id"])
        ) is None


async def test_projection_query_bound_and_event_order_is_sequence_not_timestamp(
    db_client: AsyncClient,
    runtime_db_engine: AsyncEngine,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    quote = await _create_quote(db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"])
    await _accept_quote(db_client, quote["id"])
    assert (await db_client.post(f"/api/v1/quotes/{quote['id']}/convert")).status_code == 201
    statements: list[str] = []

    def count_sql(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(runtime_db_engine.sync_engine, "before_cursor_execute", count_sql)
    try:
        response = await db_client.get(f"/api/v1/quotes/{quote['id']}/document-chain")
    finally:
        event.remove(runtime_db_engine.sync_engine, "before_cursor_execute", count_sql)
    assert response.status_code == 200
    events = response.json()["events"]
    # MODE_LOCKED and INVOICE_CREATED are one transaction and share `now()`;
    # the endpoint's sequence order must still be deterministic.
    assert len({item["occurred_at"] for item in events}) < len(events)
    assert [item["event_type"] for item in events] == ["MODE_LOCKED", "INVOICE_CREATED"]
    # Authentication and existing ORM select-in loads are fixed overhead; this
    # bound remains constant as chain node count grows and rejects an N+1 loop.
    assert len(statements) <= 15
