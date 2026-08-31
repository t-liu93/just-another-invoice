"""PostgreSQL/runtime integration coverage for M12 Step 2 chain invariants."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from test_m12_advance_integration import _additional_formal_quote, _advance_payload
from test_m12_correction_followup_integration import _issue_credit, _issued_final
from test_m12_final_integration import _issued_advance
from test_m12_refund_integration import _pay
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
from jai.services.document_chain import get_document_chain, get_invoice_document_chain

pytestmark = pytest.mark.integration


def _actions(payload: dict[str, object]) -> dict[str, bool]:
    return {
        row["code"]: row["available"]  # type: ignore[index]
        for row in payload["available_actions"]  # type: ignore[index]
    }


async def test_credit_projection_reason_and_http_command_form_one_contract(
    db_client: AsyncClient,
) -> None:
    """Projection availability/reason codes match the real command surface."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    issued = await _create_invoice(
        db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"]
    )
    source_id = issued["id"]

    async def projected(invoice_id: str) -> dict[str, object]:
        chain = await db_client.get(f"/api/v1/invoices/{invoice_id}/document-chain")
        assert chain.status_code == 200, chain.text
        return next(
            row
            for row in chain.json()["available_actions"]
            if row["code"] == "CREATE_CREDIT_NOTE" and row["target_id"] == invoice_id
        )

    positive_projection = await projected(source_id)
    assert positive_projection["available"] is True
    assert positive_projection["reason_code"] is None
    created = await db_client.post(
        f"/api/v1/invoices/{source_id}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-02"},
    )
    assert created.status_code == 201, created.text
    issued_credit = await db_client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued_credit.status_code == 200, issued_credit.text
    rows: list[tuple[dict[str, object], int, str]] = [
        (await projected(source_id), 409, "CREDIT_NO_REMAINING_BASIS"),
    ]

    for projection, expected_status, expected_code in rows:
        target_id = projection["target_id"]
        response = await db_client.post(
            f"/api/v1/invoices/{target_id}/credit-notes",
            json={"full_remaining": True, "invoice_date": "2026-02-03"},
        )
        assert response.status_code == expected_status, response.text
        assert projection["available"] is False
        assert projection["reason_code"] == expected_code
        assert response.json()["detail"]["code"] == expected_code


async def test_projection_targets_and_http_commands_keep_a_table_driven_contract(
    db_client: AsyncClient,
) -> None:
    """Exercise only real projected targets through their matching HTTP command.

    This is intentionally a compact cross-family parity table.  Per-command
    suites retain their deeper money/concurrency assertions; this one catches
    a UI-facing action becoming available (or explaining a reason) while its
    actual target route has drifted.
    """
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)

    async def action(chain_url: str, code: str, target_id: str) -> dict[str, object]:
        response = await db_client.get(chain_url)
        assert response.status_code == 200, response.text
        return next(
            row
            for row in response.json()["available_actions"]
            if row["code"] == code and row.get("target_id") == target_id
        )

    async def assert_unavailable(
        chain_url: str,
        code: str,
        target_id: str,
        request_url: str,
        body: dict[str, object],
        reason: str,
        status: int = 409,
    ) -> None:
        projected = await action(chain_url, code, target_id)
        assert projected["available"] is False
        assert projected["reason_code"] == reason
        response = await db_client.post(request_url, json=body)
        assert response.status_code == status, response.text
        assert response.json()["detail"]["code"] == reason

    # Direct Standard → Credit → follow-up establishes every direct target
    # from the chain itself, rather than a caller-invented id.
    direct = await _create_invoice(
        db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"]
    )
    direct_chain = f"/api/v1/invoices/{direct['id']}/document-chain"
    credit_action = await action(direct_chain, "CREATE_CREDIT_NOTE", direct["id"])
    assert credit_action["available"] is True and credit_action["reason_code"] is None
    credit_draft = await db_client.post(
        f"/api/v1/invoices/{credit_action['target_id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-01"},
    )
    assert credit_draft.status_code == 201, credit_draft.text
    credit_issued = await db_client.post(
        f"/api/v1/invoices/{credit_draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert credit_issued.status_code == 200, credit_issued.text
    credit_id = credit_issued.json()["id"]
    replacement = await action(direct_chain, "CREATE_REPLACEMENT", credit_id)
    assert replacement["available"] is True and replacement["reason_code"] is None
    created_replacement = await db_client.post(
        f"/api/v1/credit-notes/{replacement['target_id']}/replacement"
    )
    assert created_replacement.status_code == 201, created_replacement.text
    await assert_unavailable(
        direct_chain,
        "CREATE_COMPENSATING_INVOICE",
        credit_id,
        f"/api/v1/credit-notes/{credit_id}/compensating-invoice",
        {},
        "FOLLOWUP_ALREADY_EXISTS",
    )
    await assert_unavailable(
        direct_chain,
        "CREATE_CREDIT_NOTE",
        direct["id"],
        f"/api/v1/invoices/{direct['id']}/credit-notes",
        {"full_remaining": True, "invoice_date": "2026-02-02"},
        "CREDIT_NO_REMAINING_BASIS",
    )

    # The formal chain covers Advance calculate/create, a non-issued source,
    # Final creation, the Final-DRAFT freeze and cancellation's own endpoint.
    quote = await _additional_formal_quote(db_client, seeds)
    quote_chain = f"/api/v1/quotes/{quote['id']}/document-chain"
    advance = await action(quote_chain, "CREATE_ADVANCE", quote["id"])
    assert advance["available"] is True and advance["reason_code"] is None
    calculated = await db_client.post(
        f"/api/v1/quotes/{advance['target_id']}/advance-invoices/calculate",
        json={"input_mode": "PERCENTAGE", "percentage": "50"},
    )
    assert calculated.status_code == 200, calculated.text
    advance_draft = await db_client.post(
        f"/api/v1/quotes/{advance['target_id']}/advance-invoices", json=_advance_payload()
    )
    assert advance_draft.status_code == 201, advance_draft.text
    advance_id = advance_draft.json()["id"]
    await assert_unavailable(
        quote_chain,
        "CREATE_CREDIT_NOTE",
        advance_id,
        f"/api/v1/invoices/{advance_id}/credit-notes",
        {"full_remaining": True, "invoice_date": "2026-02-02"},
        "CREDIT_SOURCE_NOT_ISSUED",
        422,
    )
    issued_advance = await db_client.post(
        f"/api/v1/invoices/{advance_id}/status", json={"status": "SENT"}
    )
    assert issued_advance.status_code == 200, issued_advance.text
    final = await action(quote_chain, "CREATE_FINAL", quote["id"])
    assert final["available"] is True and final["reason_code"] is None
    final_draft = await db_client.post(
        f"/api/v1/quotes/{final['target_id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final_draft.status_code == 201, final_draft.text
    await assert_unavailable(
        quote_chain,
        "CREATE_CREDIT_NOTE",
        advance_id,
        f"/api/v1/invoices/{advance_id}/credit-notes",
        {"full_remaining": True, "invoice_date": "2026-03-02"},
        "FINAL_DRAFT_FREEZE",
    )
    await assert_unavailable(
        quote_chain,
        "CREATE_PROJECT_CANCELLATION",
        quote["id"],
        f"/api/v1/quotes/{quote['id']}/cancellation/preview",
        {"invoice_date": "2026-03-02"},
        "FINAL_DRAFT_FREEZE",
    )


async def test_mixed_timeline_has_every_discriminator_amount_and_causal_event_order(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    final = await _issued_final(db_client, quote["id"])
    advance_gross = str(advance["payable_before_payments"])
    incoming = await _pay(db_client, advance["id"], "60.50", payment_date="2026-03-02")
    await _pay(db_client, final["id"], "60.50", payment_date="2026-03-02")
    credit = await _issue_credit(db_client, advance["id"], invoice_date="2026-03-03")
    refunded = await db_client.post(
        f"/api/v1/credit-notes/{credit['id']}/refunds",
        json={"payment_date": "2026-03-04", "amount": "60.50"},
    )
    assert refunded.status_code == 201, refunded.text

    response = await db_client.get(f"/api/v1/quotes/{quote['id']}/document-chain")
    assert response.status_code == 200, response.text
    chain = response.json()
    timeline = chain["timeline"]
    assert {item["kind"] for item in timeline} >= {"NODE", "EVENT", "RELATION", "APPLICATION"}
    assert [item["order"] for item in timeline] == list(range(len(timeline)))
    application = next(item["application"] for item in timeline if item["kind"] == "APPLICATION")
    assert application["advance_invoice_id"] == advance["id"]
    assert application["final_invoice_id"] == final["id"]
    assert Decimal(application["gross_amount"]) == Decimal(advance_gross)
    payments = [
        item["node"]
        for item in timeline
        if item["kind"] == "NODE" and item["node"]["node_type"] == "PAYMENT"
    ]
    assert {Decimal(node["incoming_payment_amount"]) for node in payments} >= {Decimal("60.50")}
    assert {Decimal(node["refund_amount"]) for node in payments} >= {Decimal("60.50")}
    events = [item["event"] for item in timeline if item["kind"] == "EVENT"]
    event_orders = [event["event_order"] for event in events]
    assert event_orders == sorted(event_orders)
    types = [event["event_type"] for event in events]
    assert types.index("INVOICE_CREATED") < types.index("INVOICE_ISSUED")
    assert types.index("INVOICE_PAYMENT_CREATED") < types.index("REFUND_CREATED")
    assert Decimal(incoming["items"][0]["amount"]) == Decimal("60.50")


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


async def test_direct_component_actions_are_target_scoped_and_match_followup_command(
    db_client: AsyncClient,
) -> None:
    """A no-Quote Standard remains reachable from both source and Credit pages."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    source = await _create_invoice(
        db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"]
    )
    source_chain = await db_client.get(f"/api/v1/invoices/{source['id']}/document-chain")
    assert source_chain.status_code == 200, source_chain.text
    credit_action = next(
        item
        for item in source_chain.json()["available_actions"]
        if item["code"] == "CREATE_CREDIT_NOTE" and item["target_id"] == source["id"]
    )
    assert credit_action == {
        "code": "CREATE_CREDIT_NOTE",
        "available": True,
        "reason_code": None,
        "target_id": source["id"],
        "target_type": "INVOICE",
    }
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-01"},
    )
    assert draft.status_code == 201, draft.text
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    source_chain = await db_client.get(f"/api/v1/invoices/{source['id']}/document-chain")
    credit_chain = await db_client.get(f"/api/v1/invoices/{draft.json()['id']}/document-chain")
    assert source_chain.json() == credit_chain.json()
    replacement = next(
        item
        for item in credit_chain.json()["available_actions"]
        if item["code"] == "CREATE_REPLACEMENT" and item["target_id"] == draft.json()["id"]
    )
    assert replacement["available"] is True
    followup = await db_client.post(f"/api/v1/credit-notes/{draft.json()['id']}/replacement")
    assert followup.status_code == 201, followup.text


async def test_chain_get_is_read_only_and_has_a_fixed_small_statement_budget(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    runtime_db_engine: AsyncEngine,
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Regression: projection must not call command helpers with row locks."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    source = await _create_invoice(
        db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"]
    )
    statements: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    event.listen(runtime_db_engine.sync_engine, "before_cursor_execute", record)
    try:
        async with runtime_session_maker() as reader:
            # Keep the GET transaction open while another connection attempts
            # NOWAIT.  Checking after this context exits cannot detect an
            # accidental read lock because PostgreSQL has released it already.
            async with reader.begin():
                await set_rls_company(reader, seeds["company_id"])
                chain = await get_invoice_document_chain(
                    reader, company_id=seeds["company_id"], invoice_id=source["id"]
                )
                assert chain is not None
                async with admin_session_maker() as writer:
                    await writer.execute(
                        text("SELECT id FROM invoice WHERE id = :id FOR UPDATE NOWAIT"),
                        {"id": source["id"]},
                    )
    finally:
        event.remove(runtime_db_engine.sync_engine, "before_cursor_execute", record)
    # The component read has a fixed eager-load budget; it must not grow by
    # taking one command-context query per Credit Note.
    assert len(statements) <= 20
    assert not any("FOR UPDATE" in statement.upper() for statement in statements)


async def test_final_draft_freeze_projects_credit_reason_and_matches_command(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    final = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final.status_code == 201, final.text
    chain = await db_client.get(f"/api/v1/invoices/{advance['id']}/document-chain")
    action = next(
        item
        for item in chain.json()["available_actions"]
        if item["code"] == "CREATE_CREDIT_NOTE" and item["target_id"] == advance["id"]
    )
    assert action["available"] is False
    assert action["reason_code"] == "FINAL_DRAFT_FREEZE"
    cancellation = next(
        item
        for item in chain.json()["available_actions"]
        if item["code"] == "CREATE_PROJECT_CANCELLATION" and item["target_id"] == quote["id"]
    )
    assert cancellation == {
        "code": "CREATE_PROJECT_CANCELLATION",
        "available": False,
        "reason_code": "FINAL_DRAFT_FREEZE",
        "target_id": quote["id"],
        "target_type": "QUOTE",
    }
    rejected = await db_client.post(
        f"/api/v1/invoices/{advance['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-03-02"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "FINAL_DRAFT_FREEZE"


async def test_direct_chain_projection_query_count_is_constant_for_zero_one_and_three_credits(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    runtime_db_engine: AsyncEngine,
) -> None:
    """Credit DRAFTs do not reserve basis, so they form a clean 0/1/3 read probe."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    source = await _create_invoice(
        db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"]
    )

    async def count() -> int:
        statements: list[str] = []

        def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
            statements.append(statement)

        event.listen(runtime_db_engine.sync_engine, "before_cursor_execute", record)
        try:
            async with runtime_session_maker() as session:
                await set_rls_company(session, seeds["company_id"])
                assert (
                    await get_invoice_document_chain(
                        session, company_id=seeds["company_id"], invoice_id=source["id"]
                    )
                    is not None
                )
        finally:
            event.remove(runtime_db_engine.sync_engine, "before_cursor_execute", record)
        return len(statements)

    await count()
    for _ in range(1):
        created = await db_client.post(
            f"/api/v1/invoices/{source['id']}/credit-notes",
            json={"full_remaining": True, "invoice_date": "2026-02-01"},
        )
        assert created.status_code == 201, created.text
    one = await count()
    for _ in range(2):
        created = await db_client.post(
            f"/api/v1/invoices/{source['id']}/credit-notes",
            json={"full_remaining": True, "invoice_date": "2026-02-01"},
        )
        assert created.status_code == 201, created.text
    three = await count()
    # Issued-Credit projection is a bulk query: the statement shape is fixed
    # for one and three Credits, and readers never acquire command locks.
    assert one == three


async def test_quote_chain_issued_credit_projection_is_constant_and_never_locks_reader(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    runtime_db_engine: AsyncEngine,
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Formal Quote reads use bulk issued-Credit state, never command locks."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")

    async def count(*, prove_nowait: bool = False) -> int:
        statements: list[str] = []

        def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
            statements.append(statement)

        event.listen(runtime_db_engine.sync_engine, "before_cursor_execute", record)
        try:
            async with runtime_session_maker() as reader:
                async with reader.begin():
                    await set_rls_company(reader, seeds["company_id"])
                    assert (
                        await get_document_chain(
                            reader, company_id=seeds["company_id"], quote_id=quote["id"]
                        )
                        is not None
                    )
                    if prove_nowait:
                        async with admin_session_maker() as writer:
                            await writer.execute(
                                text("SELECT id FROM invoice WHERE id = :id FOR UPDATE NOWAIT"),
                                {"id": advance["id"]},
                            )
        finally:
            event.remove(runtime_db_engine.sync_engine, "before_cursor_execute", record)
        assert not any("FOR UPDATE" in statement.upper() for statement in statements)
        return len(statements)

    await count(prove_nowait=True)
    preview = await db_client.post(
        f"/api/v1/invoices/{advance['id']}/credit-notes/calculate", json={"full_remaining": True}
    )
    assert preview.status_code == 200, preview.text
    basis_id = preview.json()["lines"][0]["source_basis_line_id"]
    for _ in range(1):
        draft = await db_client.post(
            f"/api/v1/invoices/{advance['id']}/credit-notes",
            json={
                "full_remaining": False,
                "invoice_date": "2026-03-01",
                "lines": [
                    {
                        "source_basis_line_id": basis_id,
                        "input_mode": "GROSS_AMOUNT",
                        "gross_amount": "10",
                    }
                ],
            },
        )
        assert draft.status_code == 201, draft.text
        assert (
            await db_client.post(
                f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
            )
        ).status_code == 200
    one = await count()
    for _ in range(2):
        draft = await db_client.post(
            f"/api/v1/invoices/{advance['id']}/credit-notes",
            json={
                "full_remaining": False,
                "invoice_date": "2026-03-01",
                "lines": [
                    {
                        "source_basis_line_id": basis_id,
                        "input_mode": "GROSS_AMOUNT",
                        "gross_amount": "10",
                    }
                ],
            },
        )
        assert draft.status_code == 201, draft.text
        assert (
            await db_client.post(
                f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
            )
        ).status_code == 200
    three = await count()
    assert one == three


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

    assert _actions((await db_client.get(f"/api/v1/quotes/{unset['id']}/document-chain")).json())[
        "CONVERT_TO_INVOICE"
    ]
    assert (await db_client.post(f"/api/v1/quotes/{direct['id']}/convert")).status_code == 201
    assert (
        await db_client.post(
            f"/api/v1/quotes/{receipt['id']}/payments",
            json={"payment_date": "2026-01-15", "amount": "10"},
        )
    ).status_code == 201
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
        assert (
            await get_document_chain(
                session, company_id=uuid.uuid4(), quote_id=uuid.UUID(unset["id"])
            )
            is None
        )


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
    assert [item["event_order"] for item in events] == sorted(
        item["event_order"] for item in events
    )
    # The mixed typed feed preserves append-only lifecycle causality even
    # when generated UUIDs are unrelated to creation order.
    timeline_events = [
        item["event"] for item in response.json()["timeline"] if item["kind"] == "EVENT"
    ]
    assert [item["event_type"] for item in timeline_events] == [
        "MODE_LOCKED",
        "INVOICE_CREATED",
    ]
    assert [item["event_order"] for item in timeline_events] == sorted(
        item["event_order"] for item in timeline_events
    )
    assert [item["order"] for item in response.json()["timeline"]] == list(
        range(len(response.json()["timeline"]))
    )
    # Authentication and existing ORM select-in loads are fixed overhead; this
    # bound remains constant as chain node count grows and rejects an N+1 loop.
    assert len(statements) <= 15
