"""M12 Step 1 integration coverage for the additive document foundation."""
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.config import get_settings
from jai.db import reset_rls, set_rls_company
from jai.models._enums import NumberSequenceScope
from jai.schemas.setting import CreditNumberingConfig, InvoiceNumberingConfig, SmtpSettings
from jai.services.invoice import _allocate_document_vat
from jai.services.numbering import (
    DOCUMENT_TYPE_CREDIT_NOTE,
    DOCUMENT_TYPE_INVOICE,
    POSTGRES_BIGINT_MAX,
    NumberSequenceExhaustedError,
    allocate_credit_number,
    allocate_invoice_number,
)

pytestmark = pytest.mark.integration


async def _authenticate(client: AsyncClient, *, email: str = "m12@example.com") -> None:
    assert (
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "testpassword1"},
        )
    ).status_code == 201
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "m12@example.com", "password": "testpassword1"},
        )
    ).status_code == 200
    setup = await client.post("/api/v1/auth/mfa/setup")
    assert setup.status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/mfa/verify",
            json={"code": pyotp.TOTP(setup.json()["secret"]).now()},
        )
    ).status_code == 204


async def _setup_invoice_data(client: AsyncClient) -> tuple[str, str]:
    company = await client.put(
        "/api/v1/company",
        json={
            "name": "Frozen Seller",
            "legal_name": "Frozen Seller B.V.",
            "base_currency": "EUR",
            "country_code": "NL",
            "city": "Amsterdam",
        },
    )
    assert company.status_code == 200
    customer = await client.post(
        "/api/v1/customers",
        json={
            "name": "Frozen Buyer",
            "addresses": [{"type": "BILLING", "city": "Utrecht", "country_code": "NL"}],
        },
    )
    assert customer.status_code == 201
    rates = await client.get("/api/v1/vat-rates")
    assert rates.status_code == 200
    rate = next(row for row in rates.json()["items"] if row["label"] == "NL standard (21%)")
    return customer.json()["id"], rate["id"]


async def test_standard_issue_freezes_party_basis_and_defaults_supply_date(
    db_client: AsyncClient,
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A new Standard issue creates native immutable snapshots in one action."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    created = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-08-28",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [
                {
                    "name": "Installation",
                    "quantity": "2",
                    "unit_price": "100",
                    "vat_rate_id": rate_id,
                }
            ],
        },
    )
    assert created.status_code == 201
    assert created.json()["document_kind"] == "STANDARD"
    assert created.json()["party_snapshot_provenance"] is None

    issued = await db_client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    data = issued.json()
    assert data["document_kind"] == "STANDARD"
    assert data["supply_or_advance_date"] == "2026-08-28"
    assert data["issued_at"] is not None
    assert data["issued_by_user_id"] is not None
    assert data["party_snapshot_provenance"] == "NATIVE_ISSUE"
    assert data["payable_before_payments"] == data["total_incl_vat"]

    async with admin_session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT p.provenance, p.seller_name, p.buyer_name, count(b.id) AS basis_count "
                "FROM invoice_party_snapshot p "
                "LEFT JOIN invoice_credit_basis_line b ON b.invoice_id = p.invoice_id "
                "WHERE p.invoice_id = :invoice_id "
                "GROUP BY p.provenance, p.seller_name, p.buyer_name"
            ),
            {"invoice_id": created.json()["id"]},
        )
        assert rows.mappings().one() == {
            "provenance": "NATIVE_ISSUE",
            "seller_name": "Frozen Seller",
            "buyer_name": "Frozen Buyer",
            "basis_count": 1,
        }


async def test_credit_number_series_is_independent_and_forward_only(
    db_client: AsyncClient,
) -> None:
    """Credit settings preview/start/skip never mutate the invoice series."""
    await _authenticate(db_client)
    await _setup_invoice_data(db_client)
    configured = await db_client.put(
        "/api/v1/settings/credit-numbering",
        json={"template": "{{SERIES:CR}}-{{SEQUENCE:4}}", "sequence_start": 42},
    )
    assert configured.status_code == 200
    assert configured.json()["preview"] == "CR-0042"
    before = await db_client.get("/api/v1/settings/credit-number-sequence")
    assert before.status_code == 200
    assert before.json() == {"next_sequence": 42, "preview_number": "CR-0042"}
    skipped = await db_client.put(
        "/api/v1/settings/credit-number-sequence", json={"next_sequence": 60}
    )
    assert skipped.status_code == 200
    assert skipped.json() == {"next_sequence": 60, "preview_number": "CR-0060"}
    assert (
        await db_client.put("/api/v1/settings/credit-number-sequence", json={"next_sequence": 60})
    ).status_code == 422
    invoice_series = await db_client.get("/api/v1/settings/invoice-number-sequence")
    assert invoice_series.status_code == 200
    assert invoice_series.json()["next_sequence"] == 1


async def test_document_issue_basis_and_payment_cache_mutations(
    db_client: AsyncClient,
    admin_session_maker: async_sessionmaker[AsyncSession],
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """DOCUMENT snapshots and payment CRUD preserve authoritative cache parity."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    configured = await db_client.put(
        "/api/v1/settings/numbering",
        json={"template": "X-{{SEQUENCE:4}}", "sequence_start": 1},
    )
    assert configured.status_code == 200
    draft = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-08-28",
            "supply_or_advance_date": "2026-08-20",
            "tax_mode": "DOCUMENT",
            "document_vat_rate_id": rate_id,
            "amounts_include_vat": False,
            "discount": {"type": "FIXED", "value": "10"},
            "lines": [
                {"name": "A", "quantity": "1", "unit_price": "100"},
                {"name": "B", "quantity": "1", "unit_price": "200"},
            ],
        },
    )
    assert draft.status_code == 201, draft.text
    invoice_id = draft.json()["id"]
    issued = await db_client.post(f"/api/v1/invoices/{invoice_id}/status", json={"status": "SENT"})
    assert issued.status_code == 200, issued.text
    assert issued.json()["supply_or_advance_date"] == "2026-08-20"
    async with admin_session_maker() as session:
        basis = (
            (
                await session.execute(
                    text(
                        "SELECT sum(net_amount) AS net, sum(vat_amount) AS vat, "
                        "sum(gross_amount) AS gross FROM invoice_credit_basis_line "
                        "WHERE invoice_id = :id"
                    ),
                    {"id": invoice_id},
                )
            )
            .mappings()
            .one()
        )
        assert basis == {
            "net": Decimal("290.000"),
            "vat": Decimal("60.900"),
            "gross": Decimal("350.900"),
        }
    created = await db_client.post(
        f"/api/v1/invoices/{invoice_id}/payments",
        json={"payment_date": "2026-08-28", "amount": "100"},
    )
    assert created.status_code == 201, created.text
    payment_id = created.json()["items"][0]["id"]
    edited = await db_client.put(
        f"/api/v1/payments/{payment_id}",
        json={"payment_date": "2026-08-28", "amount": "350.90"},
    )
    assert edited.status_code == 200
    deleted = await db_client.delete(f"/api/v1/payments/{payment_id}")
    assert deleted.status_code == 200
    async with db_session_maker() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT due_amount, paid_status::text, incoming_payment_total, "
                        "settlement_status::text FROM invoice WHERE id = :id"
                    ),
                    {"id": invoice_id},
                )
            )
            .mappings()
            .one()
        )
        assert row == {
            "due_amount": Decimal("350.900"),
            "paid_status": "UNPAID",
            "incoming_payment_total": Decimal("0.000"),
            "settlement_status": "OPEN",
        }


async def test_runtime_http_issue_read_payment_pdf_and_safe_send(
    db_client: AsyncClient,
) -> None:
    """The real app-role dependency can traverse the issued Standard output path."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    created = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-08-28",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [{"name": "Output", "quantity": "1", "unit_price": "10", "vat_rate_id": rate_id}],
        },
    )
    assert created.status_code == 201
    invoice_id = created.json()["id"]
    issued = await db_client.post(f"/api/v1/invoices/{invoice_id}/status", json={"status": "SENT"})
    assert issued.status_code == 200
    assert (await db_client.get(f"/api/v1/invoices/{invoice_id}")).status_code == 200
    assert (await db_client.get("/api/v1/invoices")).json()["total"] == 1

    payment = await db_client.post(
        f"/api/v1/invoices/{invoice_id}/payments",
        json={"payment_date": "2026-08-28", "amount": "5"},
    )
    assert payment.status_code == 201
    payment_id = payment.json()["items"][0]["id"]
    assert (
        await db_client.put(
            f"/api/v1/payments/{payment_id}",
            json={"payment_date": "2026-08-28", "amount": "10"},
        )
    ).status_code == 200
    assert (await db_client.delete(f"/api/v1/payments/{payment_id}")).status_code == 200

    pdf = await db_client.get(f"/api/v1/invoices/{invoice_id}/pdf?locale=en")
    assert pdf.status_code == 200, pdf.text
    assert pdf.content[:4] == b"%PDF"
    smtp = SmtpSettings(
        host="smtp.example.com",
        port=587,
        username="user",
        password="test-only",
        from_email="sender@example.com",
        from_name="JAI test",
        use_tls=True,
        use_ssl=False,
    )
    with (
        patch("jai.services.email._get_smtp_config", return_value=smtp),
        patch("jai.services.email._send_mail", new_callable=AsyncMock) as send_mail,
    ):
        sent = await db_client.post(
            f"/api/v1/invoices/{invoice_id}/send",
            json={"to": "customer@example.com"},
        )
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "SENT"
    send_mail.assert_awaited_once()


def test_document_basis_vat_allocation_is_deterministic_and_conserves() -> None:
    """DOCUMENT snapshots allocate persisted VAT without re-pricing line totals."""
    allocated = _allocate_document_vat([Decimal("100.00"), Decimal("190.00")], Decimal("60.90"))
    assert allocated == [Decimal("21.00"), Decimal("39.90")]
    assert sum(allocated) == Decimal("60.90")


async def test_runtime_role_rls_is_safe_after_commit_rollback_and_reset(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """The real NOSUPERUSER role never sees snapshot rows without fresh context."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    draft = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-08-28",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [{"name": "RLS", "quantity": "1", "unit_price": "10", "vat_rate_id": rate_id}],
        },
    )
    assert draft.status_code == 201
    assert (
        await db_client.post(
            f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
        )
    ).status_code == 200
    company_id = draft.json()["company_id"]

    async with runtime_session_maker() as session:
        # This is the application URL injected into FastAPI above, not an
        # owner connection with SET ROLE.  Keep a one-connection pool so the
        # fresh/commit/rollback/RESET assertions also exercise reuse.
        assert await session.scalar(text("SELECT current_user")) == get_settings().postgres_app_user
        flags = await session.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
        assert flags.one() == (False, False)
        assert await session.scalar(text("SELECT count(*) FROM invoice_party_snapshot")) == 0
        await set_rls_company(session, company_id)
        assert await session.scalar(text("SELECT count(*) FROM invoice_party_snapshot")) == 1
        await session.commit()
        # SET LOCAL becomes an empty custom GUC after COMMIT: it must deny
        # rather than raise an invalid UUID cast error.
        assert await session.scalar(text("SELECT count(*) FROM invoice_party_snapshot")) == 0
        await set_rls_company(session, company_id)
        await session.rollback()
        assert await session.scalar(text("SELECT count(*) FROM invoice_party_snapshot")) == 0
        await set_rls_company(session, company_id)
        await reset_rls(session)
        assert await session.scalar(text("SELECT count(*) FROM invoice_party_snapshot")) == 0


async def test_runtime_rls_and_document_constraints_cover_two_companies(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Runtime RLS and the native ownership/conservation guards span both roots."""
    await _authenticate(db_client)
    first_customer, first_rate = await _setup_invoice_data(db_client)

    async def create_issued(client: AsyncClient, customer_id: str, rate_id: str, name: str) -> str:
        created = await client.post(
            "/api/v1/invoices",
            json={
                "customer_id": customer_id,
                "invoice_date": "2026-08-28",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [{"name": name, "quantity": "1", "unit_price": "10", "vat_rate_id": rate_id}],
            },
        )
        assert created.status_code == 201, created.text
        assert (
            await client.post(f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"})
        ).status_code == 200
        return created.json()["id"]

    first_invoice = await create_issued(db_client, first_customer, first_rate, "First")
    second_company = uuid.uuid4()
    second_customer = uuid.uuid4()
    second_invoice = uuid.uuid4()
    second_line = uuid.uuid4()

    async with admin_session_maker() as admin:
        first_company = await admin.scalar(
            text("SELECT company_id FROM invoice WHERE id = :invoice_id"),
            {"invoice_id": first_invoice},
        )
        assert first_company is not None
        # A second production-shaped company/document is deliberately created
        # by the migration owner rather than a second web user: v1 has one
        # owner account, while RLS must still be correct for every tenant row.
        await admin.execute(
            text("INSERT INTO company (id, name, base_currency) VALUES (:id, 'Second Co', 'EUR')"),
            {"id": second_company},
        )
        await admin.execute(
            text("INSERT INTO customer (id, company_id, name) VALUES (:id, :company_id, 'Second customer')"),
            {"id": second_customer, "company_id": second_company},
        )
        await admin.execute(
            text(
                "INSERT INTO invoice (id, company_id, customer_id, invoice_number, sequence_number, "
                "invoice_date, status, paid_status, currency, exchange_rate, tax_mode, "
                "amounts_include_vat, vat_treatment_id, vat_treatment_code, vat_treatment_label, "
                "vat_treatment_effect, vat_treatment_requires_icp, discount_type, discount_value, "
                "document_discount_amount, subtotal_excl_vat, line_discount_total, taxable_amount, "
                "vat_total, total_incl_vat, due_amount, base_subtotal_excl_vat, "
                "base_line_discount_total, base_taxable_amount, base_vat_total, base_total_incl_vat, "
                "base_due_amount) "
                "SELECT :id, :company_id, :customer_id, 'SECOND-1', 1, invoice_date, 'SENT', "
                "'UNPAID', currency, exchange_rate, tax_mode, amounts_include_vat, vat_treatment_id, "
                "vat_treatment_code, vat_treatment_label, vat_treatment_effect, "
                "vat_treatment_requires_icp, discount_type, discount_value, document_discount_amount, "
                "subtotal_excl_vat, line_discount_total, taxable_amount, vat_total, total_incl_vat, "
                "total_incl_vat, base_subtotal_excl_vat, base_line_discount_total, base_taxable_amount, "
                "base_vat_total, base_total_incl_vat, base_total_incl_vat FROM invoice WHERE id = :source_id"
            ),
            {"id": second_invoice, "company_id": second_company, "customer_id": second_customer, "source_id": first_invoice},
        )
        await admin.execute(
            text(
                "INSERT INTO invoice_line (id, invoice_id, sort_order, name, quantity, unit_price, "
                "vat_rate_id, vat_rate_label, vat_rate_percent, subtotal_excl_vat, subtotal_incl_vat, "
                "line_discount_amount, document_discount_share, taxable_amount, vat_total, total_incl_vat) "
                "SELECT :id, :invoice_id, sort_order, name, quantity, unit_price, vat_rate_id, "
                "vat_rate_label, vat_rate_percent, subtotal_excl_vat, subtotal_incl_vat, "
                "line_discount_amount, document_discount_share, taxable_amount, vat_total, total_incl_vat "
                "FROM invoice_line WHERE invoice_id = :source_id"
            ),
            {"id": second_line, "invoice_id": second_invoice, "source_id": first_invoice},
        )
        await admin.execute(
            text(
                "INSERT INTO invoice_party_snapshot (company_id, invoice_id, provenance, seller_name, "
                "seller_address, buyer_name, buyer_address, locale) "
                "VALUES (:company_id, :invoice_id, 'NATIVE_ISSUE', 'Second Co', '{}'::jsonb, "
                "'Second customer', '{}'::jsonb, 'en')"
            ),
            {"company_id": second_company, "invoice_id": second_invoice},
        )
        await admin.execute(
            text(
                "INSERT INTO invoice_credit_basis_line (company_id, invoice_id, invoice_line_id, "
                "sort_order, name, quantity, vat_treatment_code, vat_treatment_effect, "
                "vat_treatment_requires_icp, net_amount, vat_amount, gross_amount, base_net_amount, "
                "base_vat_amount, base_gross_amount) "
                "VALUES (:company_id, :invoice_id, :line_id, 0, 'Second', 1, 'NL_DOMESTIC', "
                "'APPLY_RATE', false, 10, 2.1, 12.1, 10, 2.1, 12.1)"
            ),
            {"company_id": second_company, "invoice_id": second_invoice, "line_id": second_line},
        )
        await admin.commit()

        first_basis_id = await admin.scalar(
            text("SELECT id FROM invoice_credit_basis_line WHERE invoice_id = :invoice_id"),
            {"invoice_id": first_invoice},
        )
        assert first_basis_id is not None
        with pytest.raises(DBAPIError):
            async with admin.begin_nested():
                await admin.execute(
                    text("UPDATE invoice_credit_basis_line SET net_amount = -1 WHERE id = :id"),
                    {"id": first_basis_id},
                )
        with pytest.raises(DBAPIError):
            async with admin.begin_nested():
                await admin.execute(
                    text("UPDATE invoice_credit_basis_line SET gross_amount = 99 WHERE id = :id"),
                    {"id": first_basis_id},
                )
        # This is a different valid invoice and line, so the ownership trigger
        # (not merely the UUID FK) must reject the cross-company reassignment.
        with pytest.raises(DBAPIError):
            async with admin.begin_nested():
                await admin.execute(
                    text("UPDATE invoice_credit_basis_line SET invoice_id = :invoice_id WHERE id = :id"),
                    {"invoice_id": second_invoice, "id": first_basis_id},
                )

    async with runtime_session_maker() as runtime:
        await set_rls_company(runtime, first_company)
        assert await runtime.scalar(text("SELECT count(*) FROM invoice_party_snapshot")) == 1
        assert await runtime.scalar(text("SELECT count(*) FROM invoice_credit_basis_line")) == 1
        # The target row exists, but RLS makes it invisible and non-updatable.
        changed = await runtime.execute(
            text("UPDATE invoice_party_snapshot SET seller_name = 'leak' WHERE invoice_id = :id"),
            {"id": second_invoice},
        )
        assert changed.rowcount == 0
        await runtime.commit()
        assert await runtime.scalar(text("SELECT count(*) FROM invoice_party_snapshot")) == 0
        await set_rls_company(runtime, second_company)
        assert await runtime.scalar(text("SELECT count(*) FROM invoice_party_snapshot")) == 1
        assert await runtime.scalar(text("SELECT count(*) FROM invoice_credit_basis_line")) == 1

    # Raw DB deletion proves the two snapshot roots are owned by FK cascade,
    # independent of service code and without hand-written deletion paths.
    async with admin_session_maker() as admin:
        await admin.execute(text("DELETE FROM invoice WHERE id = :id"), {"id": second_invoice})
        await admin.commit()
        assert await admin.scalar(
            text("SELECT count(*) FROM invoice_party_snapshot WHERE invoice_id = :id"),
            {"id": second_invoice},
        ) == 0
        assert await admin.scalar(
            text("SELECT count(*) FROM invoice_credit_basis_line WHERE invoice_id = :id"),
            {"id": second_invoice},
        ) == 0


async def test_credit_first_collision_advances_standard_counter_in_shared_namespace(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A committed Credit-shaped suffix cannot permanently block Standard issue."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    configured = await db_client.put(
        "/api/v1/settings/numbering",
        json={"template": "X-{{SEQUENCE:4}}", "sequence_start": 1},
    )
    assert configured.status_code == 200
    draft = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-08-28",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [
                {
                    "name": "Collision",
                    "quantity": "1",
                    "unit_price": "10",
                    "vat_rate_id": rate_id,
                }
            ],
        },
    )
    assert draft.status_code == 201
    assert (
        await db_client.post(
            f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
        )
    ).status_code == 200
    company_id = draft.json()["company_id"]

    async with db_session_maker() as session:
        # Standard issue consumed X-0001 and left its counter at 2.  Let the
        # separate Credit allocator advance past X-0001, then persist its
        # X-0002 suffix in the shared Invoice namespace before Standard asks.
        credit_number, _ = await allocate_credit_number(
            session,
            company_id,
            date(2026, 8, 28),
            numbering_config=CreditNumberingConfig(template="X-{{SEQUENCE:4}}"),
        )
        assert credit_number == "X-0002"
        await session.execute(
            text("UPDATE invoice SET invoice_number = :number WHERE id = :id"),
            {"number": credit_number, "id": draft.json()["id"]},
        )
        await session.commit()

        number, company_sequence, customer_sequence = await allocate_invoice_number(
            session,
            company_id,
            uuid.UUID(customer_id),
            date(2026, 8, 28),
            numbering_config=InvoiceNumberingConfig(template="X-{{SEQUENCE:4}}"),
            customer_invoice_prefix=None,
        )
        assert (number, company_sequence, customer_sequence) == ("X-0003", 3, None)
        await session.rollback()  # rollback proves the candidate has no partial row/counter.


async def test_shared_number_namespace_serializes_real_standard_credit_races(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Concurrent Standard/Credit allocation persists two distinct shared suffixes."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    body = {
        "customer_id": customer_id,
        "invoice_date": "2026-08-28",
        "tax_mode": "LINE",
        "amounts_include_vat": False,
        "lines": [{"name": "Race", "quantity": "1", "unit_price": "10", "vat_rate_id": rate_id}],
    }
    first = await db_client.post("/api/v1/invoices", json=body)
    second = await db_client.post("/api/v1/invoices", json=body)
    assert first.status_code == second.status_code == 201
    company_id = uuid.UUID(first.json()["company_id"])
    invoice_date = date(2026, 8, 28)

    async def standard() -> tuple[str, int]:
        async with db_session_maker() as session:
            number, sequence, _ = await allocate_invoice_number(
                session,
                company_id,
                uuid.UUID(customer_id),
                invoice_date,
                numbering_config=InvoiceNumberingConfig(template="RACE-{{SEQUENCE:1}}"),
                customer_invoice_prefix=None,
            )
            await session.execute(
                text("UPDATE invoice SET invoice_number = :number WHERE id = :id"),
                {"number": number, "id": first.json()["id"]},
            )
            await session.commit()
            return number, sequence

    async def credit() -> tuple[str, int]:
        async with db_session_maker() as session:
            number, sequence = await allocate_credit_number(
                session,
                company_id,
                invoice_date,
                numbering_config=CreditNumberingConfig(template="RACE-{{SEQUENCE:1}}"),
            )
            await session.execute(
                text(
                    "UPDATE invoice SET invoice_number = :number, document_kind = 'CREDIT_NOTE' "
                    "WHERE id = :id"
                ),
                {"number": number, "id": second.json()["id"]},
            )
            await session.commit()
            return number, sequence

    allocated = await asyncio.gather(standard(), credit())
    assert {item[0] for item in allocated} == {"RACE-1", "RACE-2"}
    assert {item[1] for item in allocated} == {1, 2}


async def test_numbering_exhausts_after_one_thousand_occupied_candidates(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """The bounded retry has a real persisted-namespace regression, not a mock."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    draft = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-08-28",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [{"name": "Occupied", "quantity": "1", "unit_price": "10", "vat_rate_id": rate_id}],
        },
    )
    assert draft.status_code == 201
    company_id = uuid.UUID(draft.json()["company_id"])
    async with db_session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO invoice (id, company_id, customer_id, invoice_number, sequence_number, "
                "invoice_date, status, paid_status, currency, exchange_rate, tax_mode, "
                "amounts_include_vat, vat_treatment_id, vat_treatment_code, vat_treatment_label, "
                "vat_treatment_effect, vat_treatment_requires_icp, discount_type, discount_value, "
                "document_discount_amount, subtotal_excl_vat, line_discount_total, taxable_amount, "
                "vat_total, total_incl_vat, due_amount, base_subtotal_excl_vat, "
                "base_line_discount_total, base_taxable_amount, base_vat_total, base_total_incl_vat, "
                "base_due_amount) "
                "SELECT gen_random_uuid(), i.company_id, i.customer_id, 'OCC-' || n, n, "
                "i.invoice_date, 'DRAFT', 'UNPAID', i.currency, i.exchange_rate, i.tax_mode, "
                "i.amounts_include_vat, i.vat_treatment_id, i.vat_treatment_code, "
                "i.vat_treatment_label, i.vat_treatment_effect, i.vat_treatment_requires_icp, "
                "i.discount_type, i.discount_value, i.document_discount_amount, i.subtotal_excl_vat, "
                "i.line_discount_total, i.taxable_amount, i.vat_total, i.total_incl_vat, "
                "i.due_amount, i.base_subtotal_excl_vat, i.base_line_discount_total, "
                "i.base_taxable_amount, i.base_vat_total, i.base_total_incl_vat, i.base_due_amount "
                "FROM invoice i CROSS JOIN generate_series(1, 1000) AS n WHERE i.id = :source_id"
            ),
            {"source_id": draft.json()["id"]},
        )
        await session.commit()
        with pytest.raises(NumberSequenceExhaustedError) as exhausted:
            await allocate_invoice_number(
                session,
                company_id,
                uuid.UUID(customer_id),
                date(2026, 8, 28),
                numbering_config=InvoiceNumberingConfig(template="OCC-{{SEQUENCE:1}}"),
                customer_invoice_prefix=None,
            )
        assert exhausted.value.code == "NUMBER_SEQUENCE_EXHAUSTED"
        await session.rollback()


async def test_generic_calculator_rejects_m12_intent_and_dedicated_components_are_explicit(
    db_client: AsyncClient,
) -> None:
    """Contract rejects silent Standard intent loss while reserving exact routes."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    standard = {
        "customer_id": customer_id,
        "invoice_date": "2026-08-28",
        "tax_mode": "LINE",
        "amounts_include_vat": False,
        "lines": [{"name": "Preview", "quantity": "1", "unit_price": "10", "vat_rate_id": rate_id}],
    }
    rejected = await db_client.post(
        "/api/v1/invoices/calculate", json={**standard, "advance_input_mode": "PERCENTAGE"}
    )
    assert rejected.status_code == 422
    advance = await db_client.post(
        "/api/v1/quotes/00000000-0000-0000-0000-000000000000/advance-invoices/calculate",
        json={"input_mode": "PERCENTAGE", "percentage": "20"},
    )
    assert advance.status_code == 404
    credit = await db_client.post(
        "/api/v1/invoices/00000000-0000-0000-0000-000000000000/credit-notes/calculate",
        json={"full_remaining": True},
    )
    # Step 5 implements the dedicated Credit calculator.  Its normal missing
    # source response is now the same non-enumerating 404 as Advance.
    assert credit.status_code == 404


async def test_wrong_m12_command_intent_is_422_without_standard_side_effects(
    db_client: AsyncClient,
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Wrong generic/dedicated commands never discard M12 intent into Standard data."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    standard = {
        "customer_id": customer_id,
        "invoice_date": "2026-08-28",
        "tax_mode": "LINE",
        "amounts_include_vat": False,
        "lines": [{"name": "Strict", "quantity": "1", "unit_price": "10", "vat_rate_id": rate_id}],
    }

    async def state() -> dict[str, object]:
        """All roots/counters that a rejected intent is forbidden to mutate."""
        async with admin_session_maker() as session:
            return (
                await session.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM invoice) AS invoices, "
                        "(SELECT count(*) FROM payment) AS payments, "
                        "(SELECT count(*) FROM invoice_party_snapshot) AS parties, "
                        "(SELECT count(*) FROM invoice_credit_basis_line) AS basis, "
                        "COALESCE((SELECT jsonb_agg(jsonb_build_object("
                        "'document_type', document_type, 'scope', scope, 'next_value', next_value) "
                        "ORDER BY document_type, scope) FROM number_sequence), '[]'::jsonb) AS counters"
                    )
                )
            ).mappings().one()

    before = await state()
    # Generic create/status reject document/source/advance intent before any
    # service call, so neither an invoice row nor a number counter is changed.
    for extra in (
        {"document_kind": "CREDIT_NOTE"},
        {"source_invoice_id": str(uuid.uuid4())},
        {"advance_input_mode": "PERCENTAGE"},
    ):
        rejected = await db_client.post("/api/v1/invoices", json={**standard, **extra})
        assert rejected.status_code == 422
        assert await state() == before
    assert (await db_client.get("/api/v1/invoices")).json()["total"] == 0
    assert (await db_client.get("/api/v1/settings/invoice-number-sequence")).json()[
        "next_sequence"
    ] == 1

    created = await db_client.post("/api/v1/invoices", json=standard)
    assert created.status_code == 201
    invoice_id = created.json()["id"]
    # Generic UPDATE is just as strict as CREATE: a rejected M12 command must
    # not rewrite the existing Standard row, payment rows, or either counter.
    for extra in (
        {"document_kind": "CREDIT_NOTE"},
        {"source_invoice_id": str(uuid.uuid4())},
        {"advance_input_mode": "PERCENTAGE"},
    ):
        rejected = await db_client.put(f"/api/v1/invoices/{invoice_id}", json={**standard, **extra})
        assert rejected.status_code == 422
        assert await state() == {
            **before,
            "invoices": 1,
        }
    invoice_after_rejections = await db_client.get(f"/api/v1/invoices/{invoice_id}")
    assert invoice_after_rejections.status_code == 200
    assert invoice_after_rejections.json()["status"] == "DRAFT"
    assert (await db_client.get("/api/v1/invoices")).json()["total"] == 1
    assert (await db_client.get(f"/api/v1/invoices/{invoice_id}/payments")).json()["items"] == []
    assert (await db_client.get("/api/v1/settings/invoice-number-sequence")).json()[
        "next_sequence"
    ] == 1
    assert (await db_client.get("/api/v1/settings/credit-number-sequence")).json()[
        "next_sequence"
    ] == 1
    before_status = await state()
    assert (
        await db_client.post(
            f"/api/v1/invoices/{invoice_id}/status",
            json={"status": "SENT", "document_kind": "CREDIT_NOTE"},
        )
    ).status_code == 422
    assert await state() == before_status
    assert (await db_client.get(f"/api/v1/invoices/{invoice_id}")).json()["status"] == "DRAFT"

    # Dedicated shapes forbid frontend-derived net/VAT values, including
    # nested Credit lines.  Validation runs before their Step 3/5 placeholders.
    advance = await db_client.post(
        "/api/v1/quotes/00000000-0000-0000-0000-000000000000/advance-invoices/calculate",
        json={"input_mode": "PERCENTAGE", "percentage": "20", "net_amount": "999"},
    )
    assert advance.status_code == 422
    credit = await db_client.post(
        "/api/v1/invoices/00000000-0000-0000-0000-000000000000/credit-notes/calculate",
        json={
            "full_remaining": False,
            "lines": [
                {
                    "source_basis_line_id": str(uuid.uuid4()),
                    "input_mode": "GROSS_AMOUNT",
                    "gross_amount": "1",
                    "vat_amount": "999",
                }
            ],
        },
    )
    assert credit.status_code == 422

    before_payment = await state()
    assert (
        await db_client.post(
            f"/api/v1/invoices/{invoice_id}/payments",
            json={
                "payment_date": "2026-08-28",
                "amount": "1",
                "direction": "REFUND",
                "credit_note_id": str(uuid.uuid4()),
            },
        )
    ).status_code == 422
    assert await state() == before_payment
    payments = await db_client.get(f"/api/v1/invoices/{invoice_id}/payments")
    assert payments.status_code == 200
    assert payments.json()["items"] == []


async def test_standard_and_credit_sequence_exhaustion_is_symmetric_and_rollback_safe(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Both shared-series allocators use the same stable overflow boundary."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    created = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-08-28",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [{"name": "Max", "quantity": "1", "unit_price": "10", "vat_rate_id": rate_id}],
        },
    )
    assert created.status_code == 201
    issued = await db_client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200
    company_id = uuid.UUID(created.json()["company_id"])

    async with db_session_maker() as session:
        # The final legal BIGINT candidate remains allocatable while free.
        await session.execute(
            text("UPDATE invoice SET invoice_number = :number WHERE id = :id"),
            {"number": "X-0001", "id": created.json()["id"]},
        )
        await session.execute(
            text(
                "UPDATE number_sequence SET next_value = :value "
                "WHERE company_id = :company_id AND document_type = :document_type "
                "AND scope = :scope"
            ),
            {
                "value": POSTGRES_BIGINT_MAX,
                "company_id": company_id,
                "document_type": DOCUMENT_TYPE_INVOICE,
                "scope": NumberSequenceScope.COMPANY.value,
            },
        )
        await session.commit()
        final_number, final_sequence, _ = await allocate_invoice_number(
            session,
            company_id,
            uuid.UUID(customer_id),
            date(2026, 8, 28),
            numbering_config=InvoiceNumberingConfig(template="X-{{SEQUENCE:1}}"),
            customer_invoice_prefix=None,
        )
        assert (final_number, final_sequence) == (f"X-{POSTGRES_BIGINT_MAX}", POSTGRES_BIGINT_MAX)
        await session.rollback()
        # Occupy that candidate afterwards: neither series may overflow or
        # leave a partial counter update.
        await session.execute(
            text("UPDATE invoice SET invoice_number = :number WHERE id = :id"),
            {"number": f"X-{POSTGRES_BIGINT_MAX}", "id": created.json()["id"]},
        )
        await session.commit()
        with pytest.raises(NumberSequenceExhaustedError) as standard_error:
            await allocate_invoice_number(
                session,
                company_id,
                uuid.UUID(customer_id),
                date(2026, 8, 28),
                numbering_config=InvoiceNumberingConfig(template="X-{{SEQUENCE:1}}"),
                customer_invoice_prefix=None,
            )
        assert standard_error.value.code == "NUMBER_SEQUENCE_EXHAUSTED"
        await session.rollback()
        standard_counter = await session.scalar(
            text(
                "SELECT next_value FROM number_sequence WHERE company_id = :company_id "
                "AND document_type = :document_type AND scope = :scope"
            ),
            {
                "company_id": company_id,
                "document_type": DOCUMENT_TYPE_INVOICE,
                "scope": NumberSequenceScope.COMPANY.value,
            },
        )
        assert standard_counter == POSTGRES_BIGINT_MAX

        with pytest.raises(NumberSequenceExhaustedError) as credit_error:
            await allocate_credit_number(
                session,
                company_id,
                date(2026, 8, 28),
                numbering_config=CreditNumberingConfig(
                    template="X-{{SEQUENCE:1}}", sequence_start=POSTGRES_BIGINT_MAX
                ),
            )
        assert credit_error.value.code == standard_error.value.code
        await session.rollback()
        credit_counter = await session.scalar(
            text(
                "SELECT next_value FROM number_sequence WHERE company_id = :company_id "
                "AND document_type = :document_type AND scope = :scope"
            ),
            {
                "company_id": company_id,
                "document_type": DOCUMENT_TYPE_CREDIT_NOTE,
                "scope": NumberSequenceScope.COMPANY.value,
            },
        )
        assert credit_counter is None


async def test_standard_lifecycle_conflict_has_a_stable_machine_code(
    db_client: AsyncClient,
) -> None:
    """A structurally valid but stale lifecycle command is never a text-only 422."""
    await _authenticate(db_client)
    customer_id, rate_id = await _setup_invoice_data(db_client)
    created = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-08-28",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [{"name": "Lifecycle", "quantity": "1", "unit_price": "10", "vat_rate_id": rate_id}],
        },
    )
    assert created.status_code == 201
    invoice_id = created.json()["id"]
    assert (
        await db_client.post(f"/api/v1/invoices/{invoice_id}/status", json={"status": "SENT"})
    ).status_code == 200
    stale = await db_client.post(f"/api/v1/invoices/{invoice_id}/status", json={"status": "SENT"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "INVOICE_LIFECYCLE_CONFLICT"
