"""M12 Step 4 Formal Final application integration coverage."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_m12_advance_integration import _additional_formal_quote, _advance_payload, _tail_quote
from test_quote_payment_integration import (
    _accept_quote,
    _create_customer,
    _create_quote,
    _full_auth,
    _record,
    _setup_company,
)

from jai.db import set_rls_company
from jai.models.invoice import Invoice
from jai.models.quote import Quote
from jai.schemas.invoice import InvoiceWrite
from jai.services import final as final_service
from jai.services.advance import AdvanceBucket
from jai.services.final import FinalValidationError
from jai.services.invoice import update_invoice

pytestmark = pytest.mark.integration


async def _issued_advance(client: AsyncClient, quote_id: str, percentage: str) -> dict[str, object]:
    created = await client.post(
        f"/api/v1/quotes/{quote_id}/advance-invoices",
        json=_advance_payload(percentage=percentage),
    )
    assert created.status_code == 201, created.text
    issued = await client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def test_final_applications_are_frozen_and_only_residual_is_payable(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    first = await _issued_advance(db_client, quote["id"], "50")
    final = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final.status_code == 201, final.text
    payload = final.json()
    assert payload["document_kind"] == "FINAL"
    assert Decimal(payload["total_incl_vat"]) == Decimal("121.00")
    assert Decimal(payload["payable_before_payments"]) == Decimal("60.50")
    assert Decimal(payload["original_quote_totals"]["gross_amount"]) == Decimal("121.00")
    assert len(payload["final_advance_applications"]) == 1
    assert payload["final_advance_applications"][0]["advance_invoice_id"] == first["id"]
    blocked = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload(percentage="10")
    )
    assert blocked.status_code == 409
    issued = await db_client.post(
        f"/api/v1/invoices/{payload['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    payment = await db_client.post(
        f"/api/v1/invoices/{payload['id']}/payments",
        json={"payment_date": "2026-03-02", "amount": "60.50"},
    )
    assert payment.status_code == 201, payment.text
    overpay = await db_client.post(
        f"/api/v1/invoices/{payload['id']}/payments",
        json={"payment_date": "2026-03-03", "amount": "0.01"},
    )
    assert overpay.status_code == 422


async def test_final_requires_issued_advance_and_delete_unfreezes(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    quote = await _create_quote(db_client, customer_id, seeds["rates"]["NL standard (21%)"]["id"])
    await _accept_quote(db_client, quote["id"])
    rejected = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert rejected.status_code == 409
    await _issued_advance(db_client, quote["id"], "50")
    final = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final.status_code == 201
    deleted = await db_client.delete(f"/api/v1/invoices/{final.json()['id']}")
    assert deleted.status_code == 204, deleted.text
    next_advance = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload(percentage="10")
    )
    assert next_advance.status_code == 201, next_advance.text


async def test_final_mixed_applications_are_stably_ordered_and_close_residual(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _tail_quote(db_client, seeds, include_zero_bucket=True)
    advances = [
        await _issued_advance(db_client, quote["id"], percentage)
        for percentage in ("20", "50", "30")
    ]
    final = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final.status_code == 201, final.text
    payload = final.json()
    assert Decimal(payload["payable_before_payments"]) == Decimal("0")
    assert [item["advance_invoice_id"] for item in payload["final_advance_applications"]] == [
        item["id"] for item in advances
    ]
    applied = sum(
        (Decimal(item["gross_amount"]) for item in payload["final_advance_applications"]),
        Decimal("0"),
    )
    assert applied == Decimal(payload["total_incl_vat"])
    assert {
        Decimal(tax["source_vat_rate_percent"])
        for item in payload["final_advance_applications"]
        for tax in item["taxes"]
    } == {Decimal("0"), Decimal("9"), Decimal("21")}


def _snapshot_amounts(document: dict[str, object]) -> tuple[object, ...]:
    """The fields that must be cloned before a Final becomes user-editable."""
    scalar_keys = (
        "discount_type",
        "discount_value",
        "document_discount_amount",
        "subtotal_excl_vat",
        "line_discount_total",
        "taxable_amount",
        "vat_total",
        "total_incl_vat",
    )
    line_keys = (
        "name",
        "quantity",
        "unit_price",
        "discount_type",
        "discount_value",
        "vat_rate_id",
        "vat_rate_label",
        "vat_rate_percent",
        "subtotal_excl_vat",
        "subtotal_incl_vat",
        "line_discount_amount",
        "document_discount_share",
        "taxable_amount",
        "vat_total",
        "total_incl_vat",
    )
    tax_keys = (
        "vat_rate_id",
        "vat_rate_label",
        "vat_rate_percent",
        "effective_vat_percent",
        "taxable_amount",
        "tax_amount",
    )
    return (
        tuple(document[key] for key in scalar_keys),
        tuple(
            (
                tuple(line[key] for key in line_keys),
                tuple(
                    tuple(tax[key] for key in tax_keys)
                    for tax in line.get("taxes", line.get("line_taxes", []))
                ),
            )
            for line in document["lines"]
        ),
        tuple(tuple(tax[key] for key in tax_keys) for tax in document["taxes"]),
    )


async def test_final_creation_clones_line_mixed_rate_quote_snapshots_after_vat_edits(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _tail_quote(db_client, seeds, include_zero_bucket=True)
    await _issued_advance(db_client, quote["id"], "50")
    quote_before = await db_client.get(f"/api/v1/quotes/{quote['id']}")
    assert quote_before.status_code == 200, quote_before.text
    expected = _snapshot_amounts(quote_before.json())
    for label, percent in (
        ("NL standard (21%)", "25"),
        ("NL reduced (9%)", "10"),
        ("Zero (0%)", "1"),
    ):
        changed = await db_client.put(
            f"/api/v1/vat-rates/{seeds['rates'][label]['id']}",
            json={"label": f"obsolete {label}", "percent": percent, "active": False},
        )
        assert changed.status_code == 200, changed.text
    final = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final.status_code == 201, final.text
    payload = final.json()
    assert _snapshot_amounts(payload) == expected
    assert payload["original_quote_totals"] == payload["final_totals"]
    assert all(Decimal(value) == 0 for value in payload["final_variance"].values())


async def test_final_creation_clones_document_tax_snapshot_after_vat_edit(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    created = await db_client.post(
        "/api/v1/quotes",
        json={
            "customer_id": customer_id,
            "quote_date": "2026-01-10",
            "tax_mode": "DOCUMENT",
            "amounts_include_vat": False,
            "document_vat_rate_id": seeds["rates"]["NL standard (21%)"]["id"],
            "lines": [
                {"name": "Document tax one", "quantity": "1", "unit_price": "40"},
                {"name": "Document tax two", "quantity": "1", "unit_price": "60"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    quote = await _accept_quote(db_client, created.json()["id"])
    await _issued_advance(db_client, quote["id"], "50")
    expected = _snapshot_amounts(quote)
    changed = await db_client.put(
        f"/api/v1/vat-rates/{seeds['rates']['NL standard (21%)']['id']}",
        json={"label": "obsolete document VAT", "percent": "25", "active": False},
    )
    assert changed.status_code == 200, changed.text
    final = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final.status_code == 201, final.text
    payload = final.json()
    assert _snapshot_amounts(payload) == expected
    assert payload["original_quote_totals"] == payload["final_totals"]


async def test_final_cancel_restore_are_rejected_and_only_draft_delete_unfreezes(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert created.status_code == 201, created.text
    final_id = created.json()["id"]
    cancelled = await db_client.post(
        f"/api/v1/invoices/{final_id}/status", json={"status": "CANCELLED"}
    )
    assert cancelled.status_code == 409, cancelled.text
    assert cancelled.json()["detail"]["code"] == "FINAL_DRAFT_LIFECYCLE_FORBIDDEN"
    blocked_advance = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload(percentage="10")
    )
    assert blocked_advance.status_code == 409, blocked_advance.text
    blocked_final = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert blocked_final.status_code == 409, blocked_final.text
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        event_count = await session.scalar(
            text(
                "SELECT count(*) FROM document_chain_event "
                "WHERE invoice_id = :invoice_id"
            ),
            {"invoice_id": final_id},
        )
        assert event_count == 1
        # A defensive direct-data corruption still cannot unfreeze the chain or
        # resurrect a stale Final through the public lifecycle command.
        await session.execute(
            text("UPDATE invoice SET status = 'CANCELLED' WHERE id = :invoice_id"),
            {"invoice_id": final_id},
        )
        await session.commit()
    restored = await db_client.post(
        f"/api/v1/invoices/{final_id}/status", json={"status": "DRAFT"}
    )
    assert restored.status_code == 409, restored.text
    assert restored.json()["detail"]["code"] == "FINAL_DRAFT_LIFECYCLE_FORBIDDEN"
    still_blocked = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload(percentage="10")
    )
    assert still_blocked.status_code == 409, still_blocked.text
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        await session.execute(
            text("UPDATE invoice SET status = 'DRAFT' WHERE id = :invoice_id"),
            {"invoice_id": final_id},
        )
        await session.commit()
    deleted = await db_client.delete(f"/api/v1/invoices/{final_id}")
    assert deleted.status_code == 204, deleted.text
    reopened = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/advance-invoices", json=_advance_payload(percentage="10")
    )
    assert reopened.status_code == 201, reopened.text


def _final_update_payload(final: dict, *, price: str, rate_id: str) -> dict[str, object]:
    line = final["lines"][0]
    return {
        "customer_id": final["customer_id"],
        "invoice_date": final["invoice_date"],
        "currency": final["currency"],
        "tax_mode": "LINE",
        "amounts_include_vat": False,
        "vat_treatment_id": final["vat_treatment_id"],
        "discount": {"type": "NONE", "value": "0"},
        "lines": [
            {"name": line["name"], "quantity": "1", "unit_price": price, "vat_rate_id": rate_id}
        ],
    }


def _editable_final_payload(
    final: dict[str, object], *, lines: list[dict[str, object]], invoice_date: str | None = None
) -> dict[str, object]:
    """Build a complete generic PUT body without copying stale line snapshots."""
    return {
        "customer_id": final["customer_id"],
        "invoice_date": invoice_date or final["invoice_date"],
        "currency": final["currency"],
        "tax_mode": "LINE",
        "amounts_include_vat": False,
        "vat_treatment_id": final["vat_treatment_id"],
        "discount": {"type": "NONE", "value": "0"},
        "lines": lines,
    }


async def test_final_edit_variance_and_bucket_date_context_guards(db_client: AsyncClient) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    response = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert response.status_code == 201
    final = response.json()
    increased = await db_client.put(
        f"/api/v1/invoices/{final['id']}",
        json=_final_update_payload(
            final, price="200", rate_id=seeds["rates"]["NL standard (21%)"]["id"]
        ),
    )
    assert increased.status_code == 200, increased.text
    assert Decimal(increased.json()["final_variance"]["gross_amount"]) > 0
    uncovered = await db_client.put(
        f"/api/v1/invoices/{final['id']}",
        json=_final_update_payload(
            final, price="200", rate_id=seeds["rates"]["NL reduced (9%)"]["id"]
        ),
    )
    assert uncovered.status_code == 422
    too_early = _final_update_payload(
        final, price="200", rate_id=seeds["rates"]["NL standard (21%)"]["id"]
    )
    too_early["invoice_date"] = "2026-01-01"
    early = await db_client.put(f"/api/v1/invoices/{final['id']}", json=too_early)
    assert early.status_code == 422
    wrong_customer = _final_update_payload(
        final, price="200", rate_id=seeds["rates"]["NL standard (21%)"]["id"]
    )
    wrong_customer["customer_id"] = await _create_customer(db_client)
    customer = await db_client.put(f"/api/v1/invoices/{final['id']}", json=wrong_customer)
    assert customer.status_code == 422
    wrong_currency = _final_update_payload(
        final, price="200", rate_id=seeds["rates"]["NL standard (21%)"]["id"]
    )
    wrong_currency["currency"] = "USD"
    currency = await db_client.put(f"/api/v1/invoices/{final['id']}", json=wrong_currency)
    assert currency.status_code == 422
    negative = _final_update_payload(
        final, price="1", rate_id=seeds["rates"]["NL standard (21%)"]["id"]
    )
    rejected = await db_client.put(f"/api/v1/invoices/{final['id']}", json=negative)
    assert rejected.status_code == 422


async def test_final_edit_keeps_accepted_treatment_snapshot_after_master_changes(
    db_client: AsyncClient,
    admin_session_maker: async_sessionmaker[AsyncSession],
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Live treatment edits cannot alter a Final, its frozen app, or its issue basis."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    quote_read = await db_client.get(f"/api/v1/quotes/{quote['id']}")
    assert quote_read.status_code == 200, quote_read.text
    snapshot = quote_read.json()["vat_treatment_snapshot"]
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert created.status_code == 201, created.text
    final = created.json()
    treatment = seeds["treatments"]["NL_DOMESTIC"]

    async def update_treatment(
        *, label: str, effect: str, requires_icp: bool, active: bool
    ) -> None:
        changed = await db_client.put(
            f"/api/v1/vat-treatments/{treatment['id']}",
            json={
                "code": treatment["code"],
                "label": label,
                "side": "SALES",
                "effect": effect,
                "requires_icp": requires_icp,
                "deductible": treatment["deductible"],
                "active": active,
            },
        )
        assert changed.status_code == 200, changed.text
        edited = await db_client.put(
            f"/api/v1/invoices/{final['id']}",
            json=_final_update_payload(
                final, price="200", rate_id=seeds["rates"]["NL standard (21%)"]["id"]
            ),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["vat_treatment_snapshot"] == snapshot

    # Each independent mutable master field can change after Quote acceptance;
    # even the final inactive state must not block the D7 price/rate edit.
    await update_treatment(
        label="Mutated Final label", effect="APPLY_RATE", requires_icp=False, active=True
    )
    await update_treatment(
        label="Mutated Final effect", effect="ZERO_REVERSE", requires_icp=False, active=True
    )
    await update_treatment(
        label="Mutated Final ICP", effect="APPLY_RATE", requires_icp=True, active=True
    )
    await update_treatment(
        label="Inactive Final treatment", effect="APPLY_RATE", requires_icp=False, active=False
    )

    # D7 still permits rate edits, provided the 21% bucket covering the frozen
    # Advance remains present.
    rate_edit = await db_client.put(
        f"/api/v1/invoices/{final['id']}",
        json=_editable_final_payload(
            final,
            lines=[
                {
                    "name": "Covered standard work",
                    "quantity": "1",
                    "unit_price": "200",
                    "vat_rate_id": seeds["rates"]["NL standard (21%)"]["id"],
                },
                {
                    "name": "New reduced-rate work",
                    "quantity": "1",
                    "unit_price": "10",
                    "vat_rate_id": seeds["rates"]["NL reduced (9%)"]["id"],
                },
            ],
        ),
    )
    assert rate_edit.status_code == 200, rate_edit.text
    assert rate_edit.json()["vat_treatment_snapshot"] == snapshot

    # A different ID is always a D7 treatment-context violation, independent
    # of whether it is absent or belongs to another tenant.
    wrong_id = _final_update_payload(
        final, price="200", rate_id=seeds["rates"]["NL standard (21%)"]["id"]
    )
    wrong_id["vat_treatment_id"] = str(uuid.uuid4())
    missing = await db_client.put(f"/api/v1/invoices/{final['id']}", json=wrong_id)
    assert missing.status_code == 422, missing.text
    assert missing.json()["detail"]["code"] == "FINAL_IMMUTABLE_TREATMENT"
    foreign_company_id = uuid.uuid4()
    foreign_treatment_id = uuid.uuid4()
    async with admin_session_maker() as session:
        await session.execute(
            text("INSERT INTO company (id, name, base_currency) VALUES (:id, 'Foreign Co', 'EUR')"),
            {"id": foreign_company_id},
        )
        await session.execute(
            text(
                "INSERT INTO vat_treatment "
                "(id, company_id, code, label, side, effect, requires_icp, active) "
                "VALUES (:id, :company_id, 'FOREIGN', 'Foreign', 'SALES', "
                "'APPLY_RATE', false, true)"
            ),
            {"id": foreign_treatment_id, "company_id": foreign_company_id},
        )
        await session.commit()
    wrong_id["vat_treatment_id"] = str(foreign_treatment_id)
    foreign = await db_client.put(f"/api/v1/invoices/{final['id']}", json=wrong_id)
    assert foreign.status_code == 422, foreign.text
    assert foreign.json()["detail"]["code"] == "FINAL_IMMUTABLE_TREATMENT"

    issued = await db_client.post(f"/api/v1/invoices/{final['id']}/status", json={"status": "SENT"})
    assert issued.status_code == 200, issued.text
    assert issued.json()["vat_treatment_snapshot"] == snapshot
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        application_tax = (
            await session.execute(
                text(
                    "SELECT vat_treatment_code, vat_treatment_effect, vat_treatment_requires_icp "
                    "FROM final_advance_application_tax fat "
                    "JOIN final_advance_application faa ON faa.id = fat.application_id "
                    "WHERE faa.final_invoice_id = :invoice_id"
                ),
                {"invoice_id": final["id"]},
            )
        ).one()
        basis_tax = (
            await session.execute(
                text(
                    "SELECT vat_treatment_code, vat_treatment_effect, vat_treatment_requires_icp "
                    "FROM invoice_credit_basis_line WHERE invoice_id = :invoice_id"
                ),
                {"invoice_id": final["id"]},
            )
        ).all()
    expected_tax = (snapshot["code"], snapshot["effect"], snapshot["requires_icp"])
    assert application_tax == expected_tax
    assert basis_tax and all(row == expected_tax for row in basis_tax)


async def _final_command_storage_state(
    session: AsyncSession, *, company_id: str, quote_id: str
) -> tuple[object, ...]:
    await set_rls_company(session, uuid.UUID(company_id))
    return (
        await session.execute(
            text(
                "SELECT q.settlement_mode, "
                "(SELECT count(*) FROM invoice i WHERE i.quote_id = q.id), "
                "(SELECT count(*) FROM payment p WHERE p.quote_id = q.id), "
                "(SELECT count(*) FROM document_chain_event e WHERE e.quote_id = q.id), "
                "(SELECT coalesce(string_agg(i.invoice_number, ',' ORDER BY i.invoice_number), '') "
                " FROM invoice i WHERE i.quote_id = q.id) "
                "FROM quote q WHERE q.id = :quote_id"
            ),
            {"quote_id": quote_id},
        )
    ).one()


async def test_final_create_returns_mode_conflict_for_direct_and_receipt_quotes(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A locked non-formal branch is a mode conflict, without Final-side drift."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    rate_id = seeds["rates"]["NL standard (21%)"]["id"]

    direct = await _create_quote(db_client, customer_id, rate_id)
    await _accept_quote(db_client, direct["id"])
    converted = await db_client.post(f"/api/v1/quotes/{direct['id']}/convert")
    assert converted.status_code == 201, converted.text

    receipt = await _create_quote(db_client, customer_id, rate_id)
    await _accept_quote(db_client, receipt["id"])
    await _record(db_client, receipt["id"], "10", "2026-01-15")

    for quote, expected_mode in ((direct, "DIRECT_INVOICE"), (receipt, "RECEIPT_ONLY")):
        async with db_session_maker() as session:
            before = await _final_command_storage_state(
                session, company_id=seeds["company_id"], quote_id=quote["id"]
            )
        rejected = await db_client.post(
            f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "MODE_CONFLICT"
        async with db_session_maker() as session:
            after = await _final_command_storage_state(
                session, company_id=seeds["company_id"], quote_id=quote["id"]
            )
        assert before == after
        assert after[0] == expected_mode

    # Keep the non-mode Final outcomes distinct: an UNSET Quote remains the
    # existing Final conflict, while FORMAL_ADVANCE without an issued Advance
    # remains a validation failure.  Missing/cross-tenant Quote IDs are 404.
    unset = await _create_quote(db_client, customer_id, rate_id)
    await _accept_quote(db_client, unset["id"])
    unset_final = await db_client.post(
        f"/api/v1/quotes/{unset['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert unset_final.status_code == 409, unset_final.text
    assert unset_final.json()["detail"]["code"] == "FINAL_CONFLICT"
    formal = await _create_quote(db_client, customer_id, rate_id)
    await _accept_quote(db_client, formal["id"])
    advance = await db_client.post(
        f"/api/v1/quotes/{formal['id']}/advance-invoices", json=_advance_payload(percentage="50")
    )
    assert advance.status_code == 201, advance.text
    deleted = await db_client.delete(f"/api/v1/invoices/{advance.json()['id']}")
    assert deleted.status_code == 204, deleted.text
    formal_final = await db_client.post(
        f"/api/v1/quotes/{formal['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert formal_final.status_code == 422, formal_final.text
    assert formal_final.json()["detail"]["code"] == "FINAL_REQUIRES_ISSUED_ADVANCE"
    missing = await db_client.post(
        f"/api/v1/quotes/{uuid.uuid4()}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert missing.status_code == 404, missing.text
    foreign_quote = await _create_quote(db_client, customer_id, rate_id)
    await _accept_quote(db_client, foreign_quote["id"])
    foreign_company_id = uuid.uuid4()
    async with admin_session_maker() as session:
        await session.execute(
            text("INSERT INTO company (id, name, base_currency) VALUES (:id, 'Foreign Co', 'EUR')"),
            {"id": foreign_company_id},
        )
        await session.execute(
            text("UPDATE quote SET company_id = :company_id WHERE id = :quote_id"),
            {"company_id": foreign_company_id, "quote_id": foreign_quote["id"]},
        )
        await session.commit()
    cross_company = await db_client.post(
        f"/api/v1/quotes/{foreign_quote['id']}/final-invoice",
        json={"invoice_date": "2026-03-01"},
    )
    assert cross_company.status_code == 404, cross_company.text


async def test_double_final_create_rolls_back_loser_without_number(db_client: AsyncClient) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    first, second = await asyncio.gather(
        db_client.post(
            f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
        ),
        db_client.post(
            f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
        ),
    )
    responses = sorted([first, second], key=lambda item: item.status_code)
    assert [item.status_code for item in responses] == [201, 409]
    assert responses[0].json()["invoice_number"] is None


async def test_final_issue_freezes_exact_residual_credit_basis_and_number_once(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Issue basis follows frozen applications, while a duplicate issue has no drift."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert created.status_code == 201, created.text
    final_id = created.json()["id"]
    first, second = await asyncio.gather(
        db_client.post(f"/api/v1/invoices/{final_id}/status", json={"status": "SENT"}),
        db_client.post(f"/api/v1/invoices/{final_id}/status", json={"status": "SENT"}),
    )
    responses = sorted([first, second], key=lambda item: item.status_code)
    assert [item.status_code for item in responses] == [200, 409]
    issued = responses[0].json()
    assert issued["invoice_number"] is not None
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        state = (
            await session.execute(
                text(
                    "SELECT i.total_incl_vat, i.payable_before_payments, i.due_amount, "
                    "(SELECT count(*) FROM final_advance_application "
                    " WHERE final_invoice_id = i.id), "
                    "(SELECT count(*) FROM invoice_credit_basis_line WHERE invoice_id = i.id), "
                    "(SELECT coalesce(sum(gross_amount), 0) FROM invoice_credit_basis_line "
                    " WHERE invoice_id = i.id), "
                    "(SELECT count(*) FROM document_chain_event "
                    " WHERE invoice_id = i.id AND event_type = 'INVOICE_ISSUED') "
                    "FROM invoice i WHERE i.id = :invoice_id"
                ),
                {"invoice_id": final_id},
            )
        ).one()
    assert tuple(Decimal(str(value)) for value in state[:3]) == (
        Decimal("121.000"),
        Decimal("60.500"),
        Decimal("60.500"),
    )
    assert state[3:] == (1, 1, Decimal("60.500"), 1)
    assert advance["invoice_number"] != issued["invoice_number"]


async def test_final_list_and_chain_projection_keep_full_and_residual_distinct(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    final = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final.status_code == 201, final.text
    listed = await db_client.get("/api/v1/invoices", params={"document_kind": "FINAL"})
    assert listed.status_code == 200, listed.text
    item = listed.json()["items"][0]
    assert item["document_kind"] == "FINAL"
    assert Decimal(item["total_incl_vat"]) == Decimal("121.00")
    assert Decimal(item["payable_before_payments"]) == Decimal("60.50")
    chain = await db_client.get(f"/api/v1/quotes/{quote['id']}/document-chain")
    assert chain.status_code == 200, chain.text
    actions = {action["code"]: action["available"] for action in chain.json()["available_actions"]}
    assert actions["CREATE_FINAL"] is False
    assert actions["CREATE_ADVANCE"] is False


async def test_final_application_rows_are_runtime_rls_scoped_and_fk_cascade(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert created.status_code == 201, created.text
    final_id = created.json()["id"]
    async with runtime_session_maker() as runtime:
        assert await runtime.scalar(text("SELECT count(*) FROM final_advance_application")) == 0
        await set_rls_company(runtime, seeds["company_id"])
        assert await runtime.scalar(text("SELECT count(*) FROM final_advance_application")) == 1
        assert await runtime.scalar(text("SELECT count(*) FROM final_advance_application_tax")) == 1
        await runtime.commit()
        assert await runtime.scalar(text("SELECT count(*) FROM final_advance_application")) == 0
    async with admin_session_maker() as admin:
        await admin.execute(
            text("DELETE FROM invoice WHERE id = :invoice_id"), {"invoice_id": final_id}
        )
        await admin.commit()
        assert await admin.scalar(
            text(
                "SELECT count(*) FROM final_advance_application "
                "WHERE final_invoice_id = :invoice_id"
            ),
            {"invoice_id": final_id},
        ) == 0
        assert await admin.scalar(
            text(
                "SELECT count(*) FROM final_advance_application_tax "
                "WHERE application_id NOT IN (SELECT id FROM final_advance_application)"
            )
        ) == 0


async def test_final_edit_supports_project_changes_but_keeps_residual_separate(
    db_client: AsyncClient,
) -> None:
    """A Final remains a whole-project editor; only its application residual is payable."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    final_response = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final_response.status_code == 201, final_response.text
    final = final_response.json()
    edited = await db_client.put(
        f"/api/v1/invoices/{final['id']}",
        json=_editable_final_payload(
            final,
            lines=[
                {
                    "name": "Expanded standard project",
                    "quantity": "2",
                    "unit_price": "100",
                    "discount": {"type": "PERCENTAGE", "value": "10"},
                    "vat_rate_id": seeds["rates"]["NL standard (21%)"]["id"],
                },
                {
                    "name": "New reduced add-on",
                    "quantity": "3",
                    "unit_price": "10",
                    "vat_rate_id": seeds["rates"]["NL reduced (9%)"]["id"],
                },
            ],
        ),
    )
    assert edited.status_code == 200, edited.text
    payload = edited.json()
    assert len(payload["lines"]) == 2
    assert Decimal(payload["total_incl_vat"]) == Decimal("250.50")
    # The frozen 50% Advance is 60.50, rather than a payment made against it.
    assert Decimal(payload["payable_before_payments"]) == Decimal("190.00")
    assert Decimal(payload["due_amount"]) == Decimal("190.00")
    assert Decimal(payload["final_variance"]["gross_amount"]) == Decimal("129.50")


async def test_final_invalid_generic_edit_uses_savepoint_and_leaves_no_event(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A rejected destructive reprice is immediately safe within the same session."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    response = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert response.status_code == 201, response.text
    final = response.json()
    invalid = _final_update_payload(
        final, price="1", rate_id=seeds["rates"]["NL standard (21%)"]["id"]
    )
    before = (
        Decimal(final["total_incl_vat"]),
        Decimal(final["payable_before_payments"]),
        len(final["lines"]),
    )
    async with runtime_session_maker() as session:
        with pytest.raises(FinalValidationError) as error:
            await update_invoice(
                session,
                uuid.UUID(final["id"]),
                InvoiceWrite.model_validate(invalid),
                seeds["company_id"],
                "EUR",
            )
        assert error.value.code == "FINAL_NEGATIVE_BUCKET_RESIDUAL"
        state = (
            await session.execute(
                text(
                    "SELECT total_incl_vat, payable_before_payments, "
                    "(SELECT count(*) FROM invoice_line WHERE invoice_id = :invoice_id), "
                    "(SELECT count(*) FROM document_chain_event "
                    " WHERE invoice_id = :invoice_id AND event_type = 'INVOICE_UPDATED') "
                    "FROM invoice WHERE id = :invoice_id"
                ),
                {"invoice_id": final["id"]},
            )
        ).one()
        assert (Decimal(str(state[0])), Decimal(str(state[1])), state[2]) == before
        assert state[3] == 0
        await session.rollback()


async def test_final_application_uses_exact_credit_bucket_seam_not_credited_total(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 5 can supply exact source buckets without a gross-total fallback."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)

    # A non-zero invoice aggregate alone must not erase an application: Step 4
    # has no Credit tables and deliberately does not invent a VAT split.
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    async with db_session_maker() as session:
        await session.execute(
            text("UPDATE invoice SET credited_total = total_incl_vat WHERE id = :invoice_id"),
            {"invoice_id": advance["id"]},
        )
        await session.commit()
    async def no_credit(_: object, __: object) -> list[AdvanceBucket]:
        return []

    monkeypatch.setattr(final_service, "_exact_pre_final_credit_buckets", no_credit)
    ordinary = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert ordinary.status_code == 201, ordinary.text
    assert Decimal(ordinary.json()["payable_before_payments"]) == Decimal("60.50")

    partial_quote = await _additional_formal_quote(db_client, seeds)
    partial_advance = await _issued_advance(db_client, partial_quote["id"], "50")
    partial_bucket = partial_advance["lines"][0]

    async def partially_credited(_: object, __: object) -> list[AdvanceBucket]:
        return [
            AdvanceBucket(
                uuid.UUID(partial_bucket["vat_rate_id"]),
                partial_bucket["vat_rate_label"],
                Decimal(partial_bucket["vat_rate_percent"]),
                Decimal("20.00"),
                Decimal("4.20"),
            )
        ]

    monkeypatch.setattr(final_service, "_exact_pre_final_credit_buckets", partially_credited)
    partial_final = await db_client.post(
        f"/api/v1/quotes/{partial_quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert partial_final.status_code == 201, partial_final.text
    partial_payload = partial_final.json()
    partial_application = partial_payload["final_advance_applications"][0]
    assert Decimal(partial_application["gross_amount"]) == Decimal("36.30")
    assert Decimal(partial_payload["payable_before_payments"]) == Decimal("84.70")

    # A replacement provider supplies a fully credited 21% source bucket.  It
    # removes exactly that application; the Final then charges the full project.
    other_quote = await _additional_formal_quote(db_client, seeds)
    other_advance = await _issued_advance(db_client, other_quote["id"], "50")
    bucket = other_advance["lines"][0]

    async def fully_credited(_: object, __: object) -> list[AdvanceBucket]:
        return [
            AdvanceBucket(
                uuid.UUID(bucket["vat_rate_id"]),
                bucket["vat_rate_label"],
                Decimal(bucket["vat_rate_percent"]),
                Decimal(bucket["taxable_amount"]),
                Decimal(bucket["vat_total"]),
            )
        ]

    monkeypatch.setattr(final_service, "_exact_pre_final_credit_buckets", fully_credited)
    fully_credited_final = await db_client.post(
        f"/api/v1/quotes/{other_quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert fully_credited_final.status_code == 201, fully_credited_final.text
    payload = fully_credited_final.json()
    assert payload["final_advance_applications"] == []
    assert Decimal(payload["payable_before_payments"]) == Decimal("121.00")
    assert Decimal(payload["total_incl_vat"]) == Decimal("121.00")


async def test_later_credit_provider_change_never_rewrites_frozen_application(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Credit after Final creation is an independent future event, not a rewrite."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert created.status_code == 201, created.text
    before = created.json()["final_advance_applications"]

    async def subsequent_full_credit(_: object, __: object) -> list[AdvanceBucket]:
        return [
            AdvanceBucket(
                uuid.uuid4(), "not a source bucket", Decimal("21"), Decimal("50"), Decimal("10.5")
            )
        ]

    monkeypatch.setattr(final_service, "_exact_pre_final_credit_buckets", subsequent_full_credit)
    reread = await db_client.get(f"/api/v1/invoices/{created.json()['id']}")
    assert reread.status_code == 200, reread.text
    assert reread.json()["final_advance_applications"] == before


async def test_final_application_db_arbiter_rejects_cross_chain_and_invalid_sources(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Runtime role cannot corrupt a Final's immutable same-Quote application chain."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote_a = await _additional_formal_quote(db_client, seeds)
    advance_a = await _issued_advance(db_client, quote_a["id"], "20")
    advance_a_second = await _issued_advance(db_client, quote_a["id"], "50")
    final = await db_client.post(
        f"/api/v1/quotes/{quote_a['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final.status_code == 201, final.text
    quote_b = await _additional_formal_quote(db_client, seeds)
    advance_b = await _issued_advance(db_client, quote_b["id"], "50")

    async with admin_session_maker() as session:
        foreign_company_id = uuid.uuid4()
        await session.execute(
            text("INSERT INTO company (id, name, base_currency) VALUES (:id, 'Foreign Co', 'EUR')"),
            {"id": foreign_company_id},
        )
        await session.commit()

    async with runtime_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        role = (
            await session.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        assert role == (False, False)
        application_id = (
            await session.execute(
                text(
                    "SELECT id FROM final_advance_application "
                    "WHERE final_invoice_id = :final_id ORDER BY sort_order LIMIT 1"
                ),
                {"final_id": final.json()["id"]},
            )
        ).scalar_one()
        second_application_id = (
            await session.execute(
                text(
                    "SELECT id FROM final_advance_application "
                    "WHERE advance_invoice_id = :advance_id"
                ),
                {"advance_id": advance_a_second["id"]},
            )
        ).scalar_one()

        # Valid same-chain UPDATE is accepted by the runtime NOBYPASSRLS role.
        await session.execute(
            text(
                "UPDATE final_advance_application SET sort_order = sort_order + 10 "
                "WHERE id = :id"
            ),
            {"id": application_id},
        )
        await session.commit()

        async def rejected(statement: str, params: dict[str, object]) -> None:
            await set_rls_company(session, seeds["company_id"])
            with pytest.raises(DBAPIError):
                await session.execute(text(statement), params)
            await session.rollback()
            # The same transaction factory remains usable after every DB rejection.
            await set_rls_company(session, seeds["company_id"])
            assert await session.scalar(text("SELECT 1")) == 1

        # INSERT and UPDATE both reject an Advance from another Quote in this company.
        await rejected(
            "INSERT INTO final_advance_application "
            "(id, company_id, final_invoice_id, advance_invoice_id, sort_order, "
            "advance_invoice_date, advance_invoice_number, taxable_amount, vat_amount, "
            "gross_amount, "
            "base_taxable_amount, base_vat_amount, base_gross_amount) "
            "SELECT gen_random_uuid(), company_id, final_invoice_id, :other_advance, sort_order, "
            "advance_invoice_date, advance_invoice_number, taxable_amount, vat_amount, "
            "gross_amount, "
            "base_taxable_amount, base_vat_amount, base_gross_amount "
            "FROM final_advance_application WHERE id = :id",
            {"id": application_id, "other_advance": advance_b["id"]},
        )
        await rejected(
            "UPDATE final_advance_application SET advance_invoice_id = :other_advance "
            "WHERE id = :id",
            {"id": application_id, "other_advance": advance_b["id"]},
        )
        # A tenant/company mismatch is rejected under the application role's FORCE RLS policy.
        await rejected(
            "UPDATE final_advance_application SET company_id = :foreign_company WHERE id = :id",
            {"id": application_id, "foreign_company": foreign_company_id},
        )
        # Wrong kind, missing Quote provenance and a non-issued Advance are all rejected by 0035.
        await rejected(
            "UPDATE final_advance_application SET advance_invoice_id = final_invoice_id "
            "WHERE id = :id",
            {"id": application_id},
        )
        await set_rls_company(session, seeds["company_id"])
        await session.execute(
            text("UPDATE invoice SET quote_id = NULL WHERE id = :advance_id"),
            {"advance_id": advance_a["id"]},
        )
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "UPDATE final_advance_application SET sort_order = sort_order + 1 "
                    "WHERE id = :id"
                ),
                {"id": application_id},
            )
        await session.rollback()
        await set_rls_company(session, seeds["company_id"])
        await session.execute(
            text("UPDATE invoice SET status = 'DRAFT' WHERE id = :advance_id"),
            {"advance_id": advance_a["id"]},
        )
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    "UPDATE final_advance_application SET sort_order = sort_order + 1 "
                    "WHERE id = :id"
                ),
                {"id": application_id},
            )
        await session.rollback()

        # The valid second application proves rejected attempts did not drift stored applications.
        await set_rls_company(session, seeds["company_id"])
        assert (
            await session.scalar(
                text("SELECT advance_invoice_id FROM final_advance_application WHERE id = :id"),
                {"id": second_application_id},
            )
        ) == uuid.UUID(str(advance_a_second["id"]))


async def test_final_issue_locks_canonical_charge_set_before_source_payment(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final issue locks Quote -> sorted charge rows before a source-side payment can mutate."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    first = await _issued_advance(db_client, quote["id"], "20")
    second = await _issued_advance(db_client, quote["id"], "50")
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert created.status_code == 201, created.text
    final = created.json()

    entered = asyncio.Event()
    release = asyncio.Event()
    locked_ids: list[uuid.UUID] = []
    original_validate = final_service.validate_final_issue

    async def pause_after_canonical_lock(
        session: AsyncSession,
        *,
        quote: Quote,
        final: Invoice,
        locked_invoices: Sequence[Invoice],
    ) -> None:
        rows = list(locked_invoices)
        locked_ids.extend(row.id for row in rows)
        assert locked_ids == sorted(locked_ids)
        assert {str(row.id) for row in rows} == {
            str(first["id"]),
            str(second["id"]),
            str(final.id),
        }
        entered.set()
        await release.wait()
        await original_validate(
            session,
            quote=quote,
            final=final,
            locked_invoices=locked_invoices,
        )

    monkeypatch.setattr(final_service, "validate_final_issue", pause_after_canonical_lock)
    issue_task = asyncio.create_task(
        db_client.post(f"/api/v1/invoices/{final['id']}/status", json={"status": "SENT"})
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    payment_task = asyncio.create_task(
        db_client.post(
            f"/api/v1/invoices/{first['id']}/payments",
            json={"payment_date": "2026-03-02", "amount": "1.00"},
        )
    )
    await asyncio.sleep(0)
    assert not payment_task.done(), "source payment bypassed the locked canonical charge prefix"
    release.set()
    issued, payment = await asyncio.gather(issue_task, payment_task)
    assert issued.status_code in {200, 409}, issued.text
    assert payment.status_code in {201, 409}, payment.text

    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        state = (
            await session.execute(
                text(
                    "SELECT status, invoice_number, payable_before_payments, "
                    "(SELECT count(*) FROM final_advance_application "
                    " WHERE final_invoice_id = :final_id), "
                    "(SELECT count(*) FROM document_chain_event "
                    " WHERE invoice_id = :final_id AND event_type = 'INVOICE_ISSUED') "
                    "FROM invoice WHERE id = :final_id"
                ),
                {"final_id": final["id"]},
            )
        ).one()
    if issued.status_code == 200:
        assert state[0] in {"SENT", "COMPLETED"}
        assert state[1] is not None
        assert state[3:] == (2, 1)
    else:
        assert state[0] == "DRAFT"
        assert state[1] is None
        assert state[3:] == (2, 0)
