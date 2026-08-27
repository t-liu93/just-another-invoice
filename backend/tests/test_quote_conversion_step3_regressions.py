"""Focused M11.5 Step 3 conversion and lifecycle regression coverage."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any, cast

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.models.invoice import Invoice
from jai.models.payment import Payment, PaymentTax
from jai.models.quote import Quote
from jai.services import quote as quote_service


async def _authenticate(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "owner@example.com", "password": "testpassword1"},
    )
    assert response.status_code == 201
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "testpassword1"},
    )
    assert response.status_code == 200
    response = await client.post("/api/v1/auth/mfa/setup")
    assert response.status_code == 200
    response = await client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": pyotp.TOTP(response.json()["secret"]).now()},
    )
    assert response.status_code == 204


async def _setup(client: AsyncClient) -> tuple[str, str]:
    response = await client.put(
        "/api/v1/company",
        json={"name": "Step 3 Test Co", "base_currency": "EUR", "country_code": "NL"},
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        "/api/v1/customers",
        json={
            "name": "Step 3 Customer",
            "addresses": [{"type": "BILLING", "country_code": "NL", "city": "Amsterdam"}],
        },
    )
    assert response.status_code == 201, response.text
    customer_id = response.json()["id"]
    response = await client.get("/api/v1/vat-rates")
    rate_id = next(
        item["id"] for item in response.json()["items"] if item["label"] == "NL standard (21%)"
    )
    return customer_id, rate_id


def _document_payload(
    customer_id: str,
    rate_id: str,
    *,
    price: str = "100.000",
    currency: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "customer_id": customer_id,
        "invoice_date": "2026-02-01",
        "tax_mode": "LINE",
        "amounts_include_vat": False,
        "lines": [
            {
                "name": "Service",
                "quantity": "1",
                "unit_price": price,
                "vat_rate_id": rate_id,
            }
        ],
    }
    if currency is not None:
        payload["currency"] = currency
    return payload


async def _accepted_quote(
    client: AsyncClient, customer_id: str, rate_id: str
) -> dict[str, Any]:
    payload = _document_payload(customer_id, rate_id)
    payload["quote_date"] = "2026-01-10"
    payload.pop("invoice_date")
    response = await client.post("/api/v1/quotes", json=payload)
    assert response.status_code == 201, response.text
    quote = response.json()
    for status in ("SENT", "ACCEPTED"):
        response = await client.post(
            f"/api/v1/quotes/{quote['id']}/status", json={"status": status}
        )
        assert response.status_code == 200, response.text
    return cast(dict[str, Any], quote)


async def _quote_payment(client: AsyncClient, quote_id: str, amount: str = "60.00") -> str:
    response = await client.post(
        f"/api/v1/quotes/{quote_id}/payments",
        json={"payment_date": "2026-02-01", "amount": amount},
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["items"][0]["id"])


@pytest.mark.integration
class TestQuoteConversionStep3Regressions:
    async def test_zero_advance_double_click_creates_one_draft(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        await _authenticate(db_client)
        customer_id, rate_id = await _setup(db_client)
        quote = await _accepted_quote(db_client, customer_id, rate_id)

        first, second = await asyncio.gather(
            db_client.post(f"/api/v1/quotes/{quote['id']}/convert"),
            db_client.post(f"/api/v1/quotes/{quote['id']}/convert"),
        )
        responses = sorted((first, second), key=lambda response: response.status_code)
        assert [response.status_code for response in responses] == [201, 409]
        invoice = responses[0].json()
        assert invoice["paid_status"] == "UNPAID"
        assert invoice["due_amount"] == "121.000"

        quote_after = await db_client.get(f"/api/v1/quotes/{quote['id']}")
        assert quote_after.status_code == 200, quote_after.text
        assert quote_after.json()["converted_invoice_id"] == invoice["id"]
        async with db_session_maker() as session:
            quote_row = await session.get(Quote, uuid.UUID(quote["id"]))
            assert quote_row is not None
            invoices = await session.execute(
                select(Invoice.id).where(Invoice.company_id == quote_row.company_id)
            )
            assert invoices.scalars().all() == [uuid.UUID(invoice["id"])]
        payments = await db_client.get(f"/api/v1/quotes/{quote['id']}/payments")
        assert payments.status_code == 200, payments.text
        assert payments.json()["items"] == []

    async def test_converted_payment_currency_and_base_vat_coverage_guards(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        await _authenticate(db_client)
        customer_id, rate_id = await _setup(db_client)

        foreign_quote_payload = _document_payload(customer_id, rate_id, currency="USD")
        foreign_quote_payload["quote_date"] = "2026-01-10"
        foreign_quote_payload.pop("invoice_date")
        foreign_quote = await db_client.post("/api/v1/quotes", json=foreign_quote_payload)
        assert foreign_quote.status_code == 422, foreign_quote.text

        quote = await _accepted_quote(db_client, customer_id, rate_id)
        payment_id = await _quote_payment(db_client, quote["id"])
        converted = await db_client.post(f"/api/v1/quotes/{quote['id']}/convert")
        assert converted.status_code == 201, converted.text
        invoice_id = converted.json()["id"]

        foreign = await db_client.put(
            f"/api/v1/invoices/{invoice_id}",
            json=_document_payload(customer_id, rate_id, currency="USD"),
        )
        assert foreign.status_code == 422, foreign.text
        assert "currency" in foreign.json()["detail"].lower()

        # Each snapshot dimension is independently authoritative.  Raising
        # any one above the final tax bucket must block the DRAFT edit.
        async with db_session_maker() as session:
            payment_tax = (
                await session.execute(
                    select(PaymentTax).where(PaymentTax.payment_id == uuid.UUID(payment_id))
                )
            ).scalar_one()
            for field in (
                "taxable_amount",
                "vat_amount",
                "base_taxable_amount",
                "base_vat_amount",
            ):
                original_value = getattr(payment_tax, field)
                await session.execute(
                    update(PaymentTax)
                    .where(PaymentTax.id == payment_tax.id)
                    .values(**{field: Decimal("1000.000")})
                )
                await session.commit()

                coverage = await db_client.put(
                    f"/api/v1/invoices/{invoice_id}",
                    json=_document_payload(customer_id, rate_id),
                )
                assert coverage.status_code == 422, (field, coverage.text)
                assert "VAT buckets" in coverage.json()["detail"]

                await session.execute(
                    update(PaymentTax)
                    .where(PaymentTax.id == payment_tax.id)
                    .values(**{field: original_value})
                )
                await session.commit()

    async def test_conversion_failure_rolls_back_invoice_payment_and_backlink(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _authenticate(db_client)
        customer_id, rate_id = await _setup(db_client)
        quote_read = await _accepted_quote(db_client, customer_id, rate_id)
        payment_id = await _quote_payment(db_client, quote_read["id"])

        payment_tax_snapshot: tuple[Decimal, Decimal, Decimal, Decimal] | None = None
        async with db_session_maker() as session:
            quote = await session.get(Quote, uuid.UUID(quote_read["id"]))
            payment = await session.get(Payment, uuid.UUID(payment_id))
            assert quote is not None
            assert payment is not None
            payment_tax = (
                await session.execute(
                    select(PaymentTax).where(PaymentTax.payment_id == payment.id)
                )
            ).scalar_one()
            payment_tax_snapshot = (
                payment_tax.taxable_amount,
                payment_tax.vat_amount,
                payment_tax.base_taxable_amount,
                payment_tax.base_vat_amount,
            )
            commit_called = False

            async def fail_final_commit() -> None:
                """Fail after all conversion writes are flushed, before commit."""
                nonlocal commit_called
                assert quote is not None
                assert payment is not None
                commit_called = True
                invoices = [
                    row
                    for row in session.identity_map.values()
                    if isinstance(row, Invoice) and row.company_id == quote.company_id
                ]
                assert len(invoices) == 1
                invoice = invoices[0]
                assert invoice.id is not None
                assert quote.converted_invoice_id == invoice.id
                assert payment.invoice_id == invoice.id

                # Match AsyncSession.commit's ordering: first send all INSERTs
                # and UPDATEs to PostgreSQL in this transaction, then fail before
                # it can commit.  The same-session reads prove all three writes
                # are visible inside this one uncommitted unit of work.
                await session.flush()
                persisted_invoice_id = await session.scalar(
                    select(Invoice.id).where(Invoice.id == invoice.id)
                )
                persisted_payment_invoice_id = await session.scalar(
                    select(Payment.invoice_id).where(Payment.id == payment.id)
                )
                persisted_quote_invoice_id = await session.scalar(
                    select(Quote.converted_invoice_id).where(Quote.id == quote.id)
                )
                assert persisted_invoice_id == invoice.id
                assert persisted_payment_invoice_id == invoice.id
                assert persisted_quote_invoice_id == invoice.id
                raise RuntimeError("forced final conversion commit failure")

            # The production service owns its one final commit.  Replacing it
            # only on this test session injects a fault after the invoice,
            # payment attachment and quote backlink are all flushed together,
            # but before the transaction is committed.
            monkeypatch.setattr(session, "commit", fail_final_commit)
            with pytest.raises(RuntimeError, match="forced final conversion commit failure"):
                await quote_service.convert_to_invoice(
                    session,
                    quote.id,
                    quote.company_id,
                    creator_id=None,
                )
            assert commit_called
            await session.rollback()

        assert payment_tax_snapshot is not None
        async with db_session_maker() as session:
            quote = await session.get(Quote, uuid.UUID(quote_read["id"]))
            payment = await session.get(Payment, uuid.UUID(payment_id))
            payment_tax = (
                await session.execute(
                    select(PaymentTax).where(PaymentTax.payment_id == uuid.UUID(payment_id))
                )
            ).scalar_one()
            assert quote is not None
            invoices = await session.execute(
                select(Invoice).where(Invoice.company_id == quote.company_id)
            )
            assert payment is not None
            assert quote.converted_invoice_id is None
            assert payment.invoice_id is None
            assert payment.quote_id == quote.id
            assert (
                payment_tax.taxable_amount,
                payment_tax.vat_amount,
                payment_tax.base_taxable_amount,
                payment_tax.base_vat_amount,
            ) == payment_tax_snapshot
            assert invoices.scalars().all() == []

        # A fresh API read must retain quote provenance and VAT snapshots too.
        quote_payments = await db_client.get(f"/api/v1/quotes/{quote_read['id']}/payments")
        assert quote_payments.status_code == 200, quote_payments.text
        quote_payment_data = quote_payments.json()
        assert quote_payment_data["converted_invoice_id"] is None
        assert len(quote_payment_data["items"]) == 1
        item = quote_payment_data["items"][0]
        assert item["quote_id"] == quote_read["id"]
        assert item["invoice_id"] is None
        tax_breakdown = item["tax_breakdown"]
        assert len(tax_breakdown) == 1
        assert (
            Decimal(tax_breakdown[0]["taxable_amount"]),
            Decimal(tax_breakdown[0]["vat_amount"]),
            Decimal(tax_breakdown[0]["base_taxable_amount"]),
            Decimal(tax_breakdown[0]["base_vat_amount"]),
        ) == payment_tax_snapshot

    async def test_ordinary_invoice_create_edit_issue_and_payment_still_work(
        self, db_client: AsyncClient
    ) -> None:
        await _authenticate(db_client)
        customer_id, rate_id = await _setup(db_client)
        created = await db_client.post(
            "/api/v1/invoices", json=_document_payload(customer_id, rate_id)
        )
        assert created.status_code == 201, created.text
        invoice_id = created.json()["id"]

        edited = await db_client.put(
            f"/api/v1/invoices/{invoice_id}",
            json=_document_payload(customer_id, rate_id, price="110.000"),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["due_amount"] == "133.100"
        issued = await db_client.post(
            f"/api/v1/invoices/{invoice_id}/status", json={"status": "SENT"}
        )
        assert issued.status_code == 200, issued.text
        assert issued.json()["status"] == "SENT"

        paid = await db_client.post(
            f"/api/v1/invoices/{invoice_id}/payments",
            json={"payment_date": "2026-02-02", "amount": "133.10"},
        )
        assert paid.status_code == 201, paid.text
        assert paid.json()["status"] == "COMPLETED"
        assert paid.json()["paid_status"] == "PAID"
