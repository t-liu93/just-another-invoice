"""M12 Step 6 correction follow-up and formal cancellation coverage."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from test_m12_advance_integration import _additional_formal_quote
from test_m12_credit_integration import (
    _credit_payload,
    _issued_document_standard,
    _issued_standard,
)
from test_m12_final_integration import _issued_advance
from test_quote_payment_integration import (
    _accept_quote,
    _create_customer,
    _create_quote,
    _full_auth,
    _record,
    _setup_company,
)

from jai.db import set_rls_company
from jai.services import correction_followup as followup_service
from jai.services.document_chain import get_invoice_document_chain

pytestmark = pytest.mark.integration


async def _issue_credit(
    client: AsyncClient,
    source_id: str,
    *,
    full: bool = True,
    quantity: str | None = None,
    invoice_date: str = "2026-03-02",
) -> dict[str, object]:
    payload = _credit_payload(invoice_date=invoice_date)
    if not full:
        preview = await client.post(
            f"/api/v1/invoices/{source_id}/credit-notes/calculate",
            json={"full_remaining": True},
        )
        assert preview.status_code == 200, preview.text
        payload = _credit_payload(quantity=quantity or "0.5", invoice_date=invoice_date)
        payload["lines"][0]["source_basis_line_id"] = preview.json()["lines"][0][  # type: ignore[index]
            "source_basis_line_id"
        ]
    draft = await client.post(f"/api/v1/invoices/{source_id}/credit-notes", json=payload)
    assert draft.status_code == 201, draft.text
    issued = await client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _issued_final(
    client: AsyncClient, quote_id: str, *, invoice_date: str = "2026-03-01"
) -> dict[str, object]:
    draft = await client.post(
        f"/api/v1/quotes/{quote_id}/final-invoice", json={"invoice_date": invoice_date}
    )
    assert draft.status_code == 201, draft.text
    issued = await client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


@pytest.mark.parametrize("partial", [False, True])
async def test_direct_standard_replacement_is_linked_unnumbered_and_issues_new_number(
    db_client: AsyncClient, partial: bool
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    credit = await _issue_credit(db_client, source["id"], full=not partial, quantity="1")
    response = await db_client.post(f"/api/v1/credit-notes/{credit['id']}/replacement")
    assert response.status_code == 201, response.text
    replacement = response.json()
    assert replacement["document_kind"] == "STANDARD"
    assert replacement["invoice_number"] is None
    assert replacement["replacement_of_credit_note_id"] == credit["id"]
    assert Decimal(replacement["total_incl_vat"]) == Decimal(credit["total_incl_vat"])
    assert (
        await db_client.post(f"/api/v1/credit-notes/{credit['id']}/replacement")
    ).status_code == 409
    chain = await db_client.get(f"/api/v1/invoices/{replacement['id']}/document-chain")
    assert chain.status_code == 200, chain.text
    assert (
        "REPLACEMENT_OF",
        credit["id"],
        replacement["id"],
    ) in {
        (row["relation_type"], row["from_node_id"], row["to_node_id"])
        for row in chain.json()["relations"]
    }
    events = [event for event in chain.json()["events"] if event["invoice_id"] == replacement["id"]]
    assert [event["event_type"] for event in events] == [
        "INVOICE_CREATED",
        "REPLACEMENT_CREATED",
    ]
    issued = await db_client.post(
        f"/api/v1/invoices/{replacement['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["invoice_number"] not in {None, source["invoice_number"]}
    assert (
        await db_client.delete(f"/api/v1/invoices/{replacement['id']}")
    ).status_code == 422
    retained_chain = await db_client.get(
        f"/api/v1/invoices/{replacement['id']}/document-chain"
    )
    assert any(
        relation["relation_type"] == "REPLACEMENT_OF"
        and relation["to_node_id"] == replacement["id"]
        for relation in retained_chain.json()["relations"]
    )


async def test_replacement_rejects_final_and_advance_final_freeze_then_reopens(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    advance_credit = await _issue_credit(db_client, advance["id"])
    final_draft = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-03"}
    )
    assert final_draft.status_code == 201, final_draft.text
    frozen = await db_client.post(f"/api/v1/credit-notes/{advance_credit['id']}/replacement")
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["detail"]["code"] == "FINAL_DRAFT_FREEZE"
    assert (
        await db_client.delete(f"/api/v1/invoices/{final_draft.json()['id']}")
    ).status_code == 204
    replacement = await db_client.post(f"/api/v1/credit-notes/{advance_credit['id']}/replacement")
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["document_kind"] == "ADVANCE"
    assert Decimal(replacement.json()["total_incl_vat"]) == Decimal(
        advance_credit["total_incl_vat"]
    )
    issued_replacement = await db_client.post(
        f"/api/v1/invoices/{replacement.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued_replacement.status_code == 200, issued_replacement.text
    final_quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, final_quote["id"], "50")
    final = await _issued_final(db_client, final_quote["id"], invoice_date="2026-03-05")
    final_credit = await _issue_credit(db_client, final["id"], invoice_date="2026-03-06")
    rejected = await db_client.post(f"/api/v1/credit-notes/{final_credit['id']}/replacement")
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "REPLACEMENT_NOT_ELIGIBLE"


async def test_cancellation_preview_maps_final_draft_freeze_without_side_effects_then_reopens(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    final_draft = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-03"}
    )
    assert final_draft.status_code == 201, final_draft.text
    frozen = await db_client.post(f"/api/v1/quotes/{quote['id']}/cancellation/preview")
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["detail"]["code"] == "FINAL_DRAFT_FREEZE"
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        assert (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM invoice WHERE quote_id = :quote_id "
                    " AND document_kind = 'CREDIT_NOTE'), "
                    "(SELECT count(*) FROM document_chain_event WHERE quote_id = :quote_id "
                    " AND event_type = 'PROJECT_CANCELLATION_CREDIT_CREATED')"
                ),
                {"quote_id": quote["id"]},
            )
        ).one() == (0, 0)
    deleted = await db_client.delete(f"/api/v1/invoices/{final_draft.json()['id']}")
    assert deleted.status_code == 204
    reopened = await db_client.post(f"/api/v1/quotes/{quote['id']}/cancellation/preview")
    assert reopened.status_code == 200, reopened.text


async def test_compensation_is_advance_pre_final_and_supplemental_standard_post_final(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    pre_quote = await _additional_formal_quote(db_client, seeds)
    pre_advance = await _issued_advance(db_client, pre_quote["id"], "50")
    pre_credit = await _issue_credit(db_client, pre_advance["id"])
    pre = await db_client.post(f"/api/v1/credit-notes/{pre_credit['id']}/compensating-invoice")
    assert pre.status_code == 201, pre.text
    assert pre.json()["document_kind"] == "ADVANCE"
    assert pre.json()["compensates_credit_note_id"] == pre_credit["id"]
    assert Decimal(pre.json()["total_incl_vat"]) == Decimal(pre_credit["total_incl_vat"])
    # A compensation is a precise mirror, not a free new Advance command.
    blocked_edit = await db_client.put(
        f"/api/v1/advance-invoices/{pre.json()['id']}",
        json={
            "input_mode": "GROSS_AMOUNT",
            "gross_amount": "1.00",
            "invoice_date": "2026-03-03",
        },
    )
    assert blocked_edit.status_code == 409, blocked_edit.text
    assert (
        await db_client.post(f"/api/v1/invoices/{pre.json()['id']}/status", json={"status": "SENT"})
    ).status_code == 200

    post_quote = await _additional_formal_quote(db_client, seeds)
    post_advance = await _issued_advance(db_client, post_quote["id"], "50")
    await _issued_final(db_client, post_quote["id"])
    post_credit = await _issue_credit(db_client, post_advance["id"], invoice_date="2026-03-04")
    post = await db_client.post(f"/api/v1/credit-notes/{post_credit['id']}/compensating-invoice")
    assert post.status_code == 201, post.text
    assert post.json()["document_kind"] == "STANDARD"
    assert post.json()["quote_id"] == post_quote["id"]
    assert Decimal(post.json()["total_incl_vat"]) == Decimal(post_credit["total_incl_vat"])
    assert (
        await db_client.post(f"/api/v1/credit-notes/{post_credit['id']}/compensating-invoice")
    ).status_code == 409


async def test_credit_followup_paths_are_mutually_exclusive_and_draft_deletion_reopens_choice(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    credit = await _issue_credit(db_client, source["id"])
    replacement = await db_client.post(f"/api/v1/credit-notes/{credit['id']}/replacement")
    assert replacement.status_code == 201, replacement.text
    blocked = await db_client.post(
        f"/api/v1/credit-notes/{credit['id']}/compensating-invoice"
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "FOLLOWUP_ALREADY_EXISTS"
    deleted = await db_client.delete(f"/api/v1/invoices/{replacement.json()['id']}")
    assert deleted.status_code == 204
    compensation = await db_client.post(
        f"/api/v1/credit-notes/{credit['id']}/compensating-invoice"
    )
    assert compensation.status_code == 201, compensation.text
    assert (
        await db_client.post(f"/api/v1/credit-notes/{credit['id']}/replacement")
    ).status_code == 409


async def test_credit_followup_mutual_exclusion_is_concurrent_and_database_enforced(
    db_client: AsyncClient,
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    credit = await _issue_credit(db_client, source["id"])
    replacement, compensation = await asyncio.gather(
        db_client.post(f"/api/v1/credit-notes/{credit['id']}/replacement"),
        db_client.post(f"/api/v1/credit-notes/{credit['id']}/compensating-invoice"),
    )
    assert sorted((replacement.status_code, compensation.status_code)) == [201, 409]
    winner = replacement if replacement.status_code == 201 else compensation
    relation_type = "COMPENSATES_CREDIT" if winner is compensation else "REPLACEMENT_OF"
    draft = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": source["customer_id"],
            "invoice_date": "2026-03-03",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [
                {
                    "name": "Raw unique constraint probe",
                    "quantity": "1",
                    "unit_price": "1",
                    "vat_rate_id": seeds["rates"]["NL standard (21%)"]["id"],
                }
            ],
        },
    )
    assert draft.status_code == 201, draft.text
    other_type = "REPLACEMENT_OF" if relation_type == "COMPENSATES_CREDIT" else "COMPENSATES_CREDIT"
    async with admin_session_maker() as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "INSERT INTO invoice_relation "
                    "(company_id, invoice_id, related_credit_note_id, relation_type) "
                    "VALUES (:company_id, :invoice_id, :credit_id, "
                    "CAST(:relation_type AS invoicerelationtype))"
                ),
                {
                    "company_id": seeds["company_id"],
                    "invoice_id": draft.json()["id"],
                    "credit_id": credit["id"],
                    "relation_type": other_type,
                },
            )
        await session.rollback()
        assert await session.scalar(
            text("SELECT count(*) FROM invoice_relation WHERE related_credit_note_id = :credit_id"),
            {"credit_id": credit["id"]},
        ) == 1


@pytest.mark.parametrize("followup_path", ["replacement", "compensating-invoice"])
async def test_direct_multigeneration_chain_is_complete_from_every_member_and_query_bounded(
    db_client: AsyncClient,
    runtime_db_engine: AsyncEngine,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    followup_path: str,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    first_credit = await _issue_credit(db_client, source["id"])
    first_positive_response = await db_client.post(
        f"/api/v1/credit-notes/{first_credit['id']}/{followup_path}"
    )
    assert first_positive_response.status_code == 201, first_positive_response.text
    first_positive = first_positive_response.json()
    assert (
        await db_client.post(
            f"/api/v1/invoices/{first_positive['id']}/status", json={"status": "SENT"}
        )
    ).status_code == 200

    async def read_with_count(invoice_id: str) -> tuple[Response, int]:
        statements: list[str] = []

        def count_sql(*args: object) -> None:
            statements.append(str(args[2]))

        event.listen(runtime_db_engine.sync_engine, "before_cursor_execute", count_sql)
        try:
            response = await db_client.get(f"/api/v1/invoices/{invoice_id}/document-chain")
        finally:
            event.remove(runtime_db_engine.sync_engine, "before_cursor_execute", count_sql)
        return response, len(statements)

    shallow_chain, shallow_count = await read_with_count(source["id"])
    assert shallow_chain.status_code == 200
    second_credit = await _issue_credit(db_client, first_positive["id"], invoice_date="2026-03-04")
    second_positive_response = await db_client.post(
        f"/api/v1/credit-notes/{second_credit['id']}/{followup_path}"
    )
    assert second_positive_response.status_code == 201, second_positive_response.text
    second_positive = second_positive_response.json()
    middle_chain, middle_count = await read_with_count(source["id"])
    assert middle_chain.status_code == 200
    assert (
        await db_client.post(
            f"/api/v1/invoices/{second_positive['id']}/status", json={"status": "SENT"}
        )
    ).status_code == 200
    third_credit = await _issue_credit(db_client, second_positive["id"], invoice_date="2026-03-05")
    third_positive_response = await db_client.post(
        f"/api/v1/credit-notes/{third_credit['id']}/{followup_path}"
    )
    assert third_positive_response.status_code == 201, third_positive_response.text
    third_positive = third_positive_response.json()
    deep_chain, deep_count = await read_with_count(source["id"])
    assert deep_chain.status_code == 200
    expected_ids = {
        source["id"],
        first_credit["id"],
        first_positive["id"],
        second_credit["id"],
        second_positive["id"],
        third_credit["id"],
        third_positive["id"],
    }
    chains = [
        deep_chain,
        *[
            await db_client.get(f"/api/v1/invoices/{invoice_id}/document-chain")
            for invoice_id in expected_ids
            if invoice_id != source["id"]
        ],
    ]
    assert all(response.status_code == 200 for response in chains)
    projections = [
        {node["id"] for node in response.json()["nodes"] if node["node_type"] == "INVOICE"}
        for response in chains
    ]
    assert all(ids == expected_ids for ids in projections)
    relation_projections = [
        {
            (relation["relation_type"], relation["from_node_id"], relation["to_node_id"])
            for relation in response.json()["relations"]
            if relation["relation_type"] != "INVOICE_TO_PAYMENT"
        }
        for response in chains
    ]
    assert all(relations == relation_projections[0] for relations in relation_projections)
    event_projections = [
        {event["id"] for event in response.json()["events"]} for response in chains
    ]
    assert all(events == event_projections[0] for events in event_projections)
    # The direct path has one CTE plus fixed bulk reads, while the existing
    # simple direct/Quote projections retain their own established bounds.
    # Growing from one to three correction generations adds no SQL round trips.
    assert {shallow_count, middle_count, deep_count} == {20}
    async with runtime_session_maker() as session:
        assert await get_invoice_document_chain(
            session, company_id=uuid.uuid4(), invoice_id=uuid.UUID(source["id"])
        ) is None


async def test_advance_replacement_dates_are_checked_on_update_and_issue(
    db_client: AsyncClient,
    admin_session_maker: async_sessionmaker[AsyncSession],
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    credit = await _issue_credit(db_client, advance["id"], invoice_date="2026-03-02")
    replacement = await db_client.post(f"/api/v1/credit-notes/{credit['id']}/replacement")
    assert replacement.status_code == 201, replacement.text
    replacement_id = replacement.json()["id"]
    update = await db_client.put(
        f"/api/v1/advance-invoices/{replacement_id}",
        json={
            "input_mode": "GROSS_AMOUNT",
            "gross_amount": replacement.json()["total_incl_vat"],
            "invoice_date": "2026-01-01",
            "supply_or_advance_date": "2026-01-01",
        },
    )
    assert update.status_code == 422, update.text
    assert update.json()["detail"]["code"] == "REPLACEMENT_DATE_BEFORE_CREDIT"
    async with admin_session_maker() as session:
        await session.execute(
            text(
                "UPDATE invoice SET invoice_date = '2026-01-01', "
                "supply_or_advance_date = '2026-01-01' WHERE id = :invoice_id"
            ),
            {"invoice_id": replacement_id},
        )
        await session.commit()
    issue = await db_client.post(
        f"/api/v1/invoices/{replacement_id}/status", json={"status": "SENT"}
    )
    assert issue.status_code == 422, issue.text
    assert issue.json()["detail"]["code"] == "REPLACEMENT_DATE_BEFORE_CREDIT"
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        assert (
            await session.execute(
                text(
                    "SELECT invoice_number, status FROM invoice WHERE id = :invoice_id"
                ),
                {"invoice_id": replacement_id},
            )
        ).one() == (None, "DRAFT")
        assert await session.scalar(
            text(
                "SELECT count(*) FROM document_chain_event "
                "WHERE invoice_id = :invoice_id AND event_type = 'INVOICE_ISSUED'"
            ),
            {"invoice_id": replacement_id},
        ) == 0


async def test_document_tax_standard_compensation_retains_snapshot_and_issues(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_document_standard(
        db_client, seeds["rates"]["NL standard (21%)"]["id"]
    )
    credit = await _issue_credit(db_client, source["id"])
    compensation = await db_client.post(
        f"/api/v1/credit-notes/{credit['id']}/compensating-invoice"
    )
    assert compensation.status_code == 201, compensation.text
    assert compensation.json()["tax_mode"] == "DOCUMENT"
    assert [
        {key: value for key, value in row.items() if key != "id"}
        for row in compensation.json()["taxes"]
    ] == [{key: value for key, value in row.items() if key != "id"} for row in credit["taxes"]]
    issued = await db_client.post(
        f"/api/v1/invoices/{compensation.json()['id']}/status",
        json={"status": "SENT"},
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["invoice_number"] is not None


async def test_receipt_only_standard_replacement_is_rejected_without_new_draft(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    quote = await _create_quote(
        db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"]
    )
    await _accept_quote(db_client, quote["id"])
    await _record(db_client, quote["id"], "24.20", "2026-02-01")
    converted = await db_client.post(f"/api/v1/quotes/{quote['id']}/convert")
    assert converted.status_code == 201, converted.text
    issued = await db_client.post(
        f"/api/v1/invoices/{converted.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    credit = await _issue_credit(
        db_client, issued.json()["id"], invoice_date=issued.json()["invoice_date"]
    )
    rejected = await db_client.post(
        f"/api/v1/credit-notes/{credit['id']}/replacement"
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "REPLACEMENT_NOT_ELIGIBLE"
    chain = await db_client.get(f"/api/v1/quotes/{quote['id']}/document-chain")
    assert not any(
        relation["relation_type"] == "REPLACEMENT_OF"
        for relation in chain.json()["relations"]
    )


async def test_formal_cancellation_creates_all_drafts_atomically_and_they_issue_independently(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advances = [
        await _issued_advance(db_client, quote["id"], percentage) for percentage in ("20", "50")
    ]
    final = await _issued_final(db_client, quote["id"])
    applications_before = final["final_advance_applications"]
    preview = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/cancellation/preview",
        json={"invoice_date": "2026-03-10", "reference_number": "CANCEL-PROJECT"},
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert {row["source_invoice_id"] for row in preview_body["sources"]} == {
        advances[0]["id"],
        advances[1]["id"],
        final["id"],
    }
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/cancellation/create-credit-drafts",
        json={
            "preview_token": preview_body["preview_token"],
            "invoice_date": "2026-03-10",
            "reference_number": "CANCEL-PROJECT",
        },
    )
    assert created.status_code == 201, created.text
    drafts = created.json()["credit_notes"]
    assert len(drafts) == 3
    assert all(row["status"] == "DRAFT" and row["invoice_number"] is None for row in drafts)
    # DRAFT cancellation Credits neither reserve basis nor rewrite applications.
    for source in [*advances, final]:
        current = await db_client.get(f"/api/v1/invoices/{source['id']}")
        assert current.json()["credit_status"] == "NOT_CREDITED"
    final_current = await db_client.get(f"/api/v1/invoices/{final['id']}")
    assert final_current.json()["final_advance_applications"] == applications_before
    first = await db_client.post(
        f"/api/v1/invoices/{drafts[0]['id']}/status", json={"status": "SENT"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["invoice_number"] is not None
    assert all(row["status"] == "DRAFT" for row in drafts[1:])


async def test_cancellation_omits_zero_remaining_and_rejects_stale_preview_without_debris(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    first = await _issued_advance(db_client, quote["id"], "20")
    second = await _issued_advance(db_client, quote["id"], "50")
    final = await _issued_final(db_client, quote["id"])
    await _issue_credit(db_client, first["id"], invoice_date="2026-03-02")
    preview = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/cancellation/preview",
        json={"invoice_date": "2026-03-10"},
    )
    assert preview.status_code == 200, preview.text
    assert first["id"] not in {row["source_invoice_id"] for row in preview.json()["sources"]}
    # Mutate one previewed basis after preview; confirmation must create none.
    await _issue_credit(
        db_client, second["id"], full=False, quantity="0.5", invoice_date="2026-03-03"
    )
    stale = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/cancellation/create-credit-drafts",
        json={
            "preview_token": preview.json()["preview_token"],
            "invoice_date": "2026-03-10",
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "CANCELLATION_PREVIEW_STALE"
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        draft_count = await session.scalar(
            text(
                "SELECT count(*) FROM invoice WHERE quote_id = :quote_id "
                "AND document_kind = 'CREDIT_NOTE' AND status = 'DRAFT'"
            ),
            {"quote_id": quote["id"]},
        )
        assert draft_count == 0
    assert (await db_client.get(f"/api/v1/invoices/{final['id']}")).status_code == 200


async def test_cancellation_includes_issued_supplemental_standard_and_keeps_applications(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    final = await _issued_final(db_client, quote["id"])
    applications = final["final_advance_applications"]
    final_credit = await _issue_credit(db_client, final["id"], invoice_date="2026-03-03")
    # Exhaust every original Formal charge first.  The compensating Standard
    # must be the sole cancellation source, not merely one row beside an
    # uncredited Advance.
    await _issue_credit(db_client, advance["id"], invoice_date="2026-03-03")
    supplemental = await db_client.post(
        f"/api/v1/credit-notes/{final_credit['id']}/compensating-invoice"
    )
    assert supplemental.status_code == 201, supplemental.text
    issued_supplemental = await db_client.post(
        f"/api/v1/invoices/{supplemental.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued_supplemental.status_code == 200, issued_supplemental.text
    preview = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/cancellation/preview",
        json={"invoice_date": "2026-03-10"},
    )
    assert preview.status_code == 200, preview.text
    assert [row["source_invoice_id"] for row in preview.json()["sources"]] == [
        supplemental.json()["id"]
    ]
    chain = await db_client.get(f"/api/v1/quotes/{quote['id']}/document-chain")
    projected = next(
        item for item in chain.json()["available_actions"]
        if item["code"] == "CREATE_PROJECT_CANCELLATION"
    )
    assert projected == {
        "code": "CREATE_PROJECT_CANCELLATION",
        "available": True,
        "reason_code": None,
        "target_id": quote["id"],
        "target_type": "QUOTE",
    }
    assert (await db_client.get(f"/api/v1/invoices/{final['id']}")).json()[
        "final_advance_applications"
    ] == applications


async def test_cancellation_races_supplemental_issue_without_deadlock_or_partial_number_events(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    final = await _issued_final(db_client, quote["id"])
    final_credit = await _issue_credit(db_client, final["id"], invoice_date="2026-03-03")
    supplemental = await db_client.post(
        f"/api/v1/credit-notes/{final_credit['id']}/compensating-invoice"
    )
    assert supplemental.status_code == 201, supplemental.text
    preview = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/cancellation/preview",
        json={"invoice_date": "2026-03-10"},
    )
    assert preview.status_code == 200, preview.text
    assert {row["source_invoice_id"] for row in preview.json()["sources"]} == {
        advance["id"]
    }

    issue_response, cancellation_response = await asyncio.wait_for(
        asyncio.gather(
            db_client.post(
                f"/api/v1/invoices/{supplemental.json()['id']}/status",
                json={"status": "SENT"},
            ),
            db_client.post(
                f"/api/v1/quotes/{quote['id']}/cancellation/create-credit-drafts",
                json={
                    "preview_token": preview.json()["preview_token"],
                    "invoice_date": "2026-03-10",
                },
            ),
        ),
        timeout=15,
    )
    assert issue_response.status_code == 200, issue_response.text
    assert issue_response.json()["invoice_number"] is not None
    assert cancellation_response.status_code in {201, 409}, cancellation_response.text
    if cancellation_response.status_code == 409:
        assert cancellation_response.json()["detail"]["code"] == "CANCELLATION_PREVIEW_STALE"

    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        issue_event_count = await session.scalar(
            text(
                "SELECT count(*) FROM document_chain_event "
                "WHERE invoice_id = :invoice_id AND event_type = 'INVOICE_ISSUED'"
            ),
            {"invoice_id": supplemental.json()["id"]},
        )
        cancellation_event_count = await session.scalar(
            text(
                "SELECT count(*) FROM document_chain_event "
                "WHERE quote_id = :quote_id "
                "AND event_type = 'PROJECT_CANCELLATION_CREDIT_CREATED'"
            ),
            {"quote_id": quote["id"]},
        )
        assert issue_event_count == 1
        assert cancellation_event_count == (
            len(cancellation_response.json()["credit_notes"])
            if cancellation_response.status_code == 201
            else 0
        )

    # DRAFT cancellation Credits do not reserve basis.  After the issue wins
    # its own transaction, a fresh preview includes the new supplemental
    # Standard as a newly visible positive source.
    next_preview = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/cancellation/preview",
        json={"invoice_date": "2026-03-10"},
    )
    assert next_preview.status_code == 200, next_preview.text
    assert {
        advance["id"],
        supplemental.json()["id"],
    }.issubset({row["source_invoice_id"] for row in next_preview.json()["sources"]})


async def test_cancellation_injected_second_create_failure_rolls_back_all_rows_and_events(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    await _issued_final(db_client, quote["id"])
    preview = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/cancellation/preview",
        json={"invoice_date": "2026-03-10"},
    )
    assert preview.status_code == 200, preview.text
    original = followup_service._persist_credit_draft
    calls = 0

    async def fail_second(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced second cancellation draft failure")
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(followup_service, "_persist_credit_draft", fail_second)
    with pytest.raises(RuntimeError, match="forced second"):
        await db_client.post(
            f"/api/v1/quotes/{quote['id']}/cancellation/create-credit-drafts",
            json={
                "preview_token": preview.json()["preview_token"],
                "invoice_date": "2026-03-10",
            },
        )
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        assert (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM invoice WHERE quote_id = :quote_id "
                    " AND document_kind = 'CREDIT_NOTE'), "
                    "(SELECT count(*) FROM invoice_correction c JOIN invoice i "
                    " ON i.id = c.credit_note_id WHERE i.quote_id = :quote_id), "
                    "(SELECT count(*) FROM document_chain_event "
                    " WHERE quote_id = :quote_id "
                    " AND event_type = 'PROJECT_CANCELLATION_CREDIT_CREATED')"
                ),
                {"quote_id": quote["id"]},
            )
        ).one() == (0, 0, 0)


async def test_relation_rls_trigger_immutability_cascade_and_concurrent_singleton(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    credit = await _issue_credit(db_client, source["id"])
    raced = await asyncio.gather(
        *[db_client.post(f"/api/v1/credit-notes/{credit['id']}/replacement") for _ in range(2)]
    )
    assert sorted(response.status_code for response in raced) == [201, 409]
    replacement = next(response.json() for response in raced if response.status_code == 201)
    foreign_company_id = uuid.uuid4()
    async with admin_session_maker() as session:
        await session.execute(
            text("INSERT INTO company (id, name, base_currency) VALUES (:id, 'Foreign', 'EUR')"),
            {"id": foreign_company_id},
        )
        await session.commit()
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "INSERT INTO invoice_relation "
                    "(company_id, invoice_id, related_credit_note_id, relation_type) "
                    "VALUES (:company_id, :invoice_id, :credit_id, 'COMPENSATES_CREDIT')"
                ),
                {
                    "company_id": foreign_company_id,
                    "invoice_id": replacement["id"],
                    "credit_id": credit["id"],
                },
            )
    async with runtime_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        assert (
            await session.scalar(
                text("SELECT count(*) FROM invoice_relation WHERE invoice_id = :invoice_id"),
                {"invoice_id": replacement["id"]},
            )
            == 1
        )
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "UPDATE invoice_relation SET relation_type = 'COMPENSATES_CREDIT' "
                    "WHERE invoice_id = :invoice_id"
                ),
                {"invoice_id": replacement["id"]},
            )
        await session.rollback()
        await set_rls_company(session, seeds["company_id"])
        with pytest.raises(DBAPIError):
            await session.execute(
                text("DELETE FROM invoice_relation WHERE invoice_id = :invoice_id"),
                {"invoice_id": replacement["id"]},
            )
        await session.rollback()
        await set_rls_company(session, foreign_company_id)
        assert await session.scalar(text("SELECT count(*) FROM invoice_relation")) == 0
    assert (await db_client.delete(f"/api/v1/invoices/{replacement['id']}")).status_code == 204
    async with admin_session_maker() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM invoice_relation WHERE invoice_id = :invoice_id"),
                {"invoice_id": replacement["id"]},
            )
            == 0
        )
