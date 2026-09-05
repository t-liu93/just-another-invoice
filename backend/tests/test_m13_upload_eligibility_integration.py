"""Runtime-role integration coverage for the M13 upload pre-body gate."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_quote_payment_integration import (
    _create_customer,
    _create_invoice,
    _full_auth,
    _setup_company,
)

from jai.models._enums import InvoiceStatus
from jai.models.company import Company
from jai.models.invoice import Invoice

pytestmark = pytest.mark.integration


async def _create_draft_invoice(
    client: AsyncClient, *, customer_id: str, rate_id: str
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-01-10",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [{
                "name": "Upload eligibility service",
                "quantity": "1",
                "unit_price": "100.000",
                "vat_rate_id": rate_id,
            }],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_runtime_role_upload_eligibility_hides_foreign_and_rejects_before_body(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
    runtime_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """The actual runtime role gates both endpoint bodies before parsing them."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    rate_id = seeds["rates"]["NL standard (21%)"]["id"]

    foreign = await _create_invoice(db_client, customer_id, rate_id)
    existing = await _create_invoice(db_client, customer_id, rate_id)
    draft = await _create_draft_invoice(db_client, customer_id=customer_id, rate_id=rate_id)
    cancelled = await _create_draft_invoice(
        db_client, customer_id=customer_id, rate_id=rate_id
    )
    rendered = await db_client.get(f"/api/v1/invoices/{existing['id']}/pdf")
    assert rendered.status_code == 200, rendered.text

    foreign_company = Company(name="Foreign upload gate", base_currency="EUR")
    async with db_session_maker() as session:
        session.add(foreign_company)
        await session.flush()
        await session.execute(
            update(Invoice)
            .where(Invoice.id == uuid.UUID(str(foreign["id"])))
            .values(company_id=foreign_company.id)
        )
        await session.execute(
            update(Invoice)
            .where(Invoice.id == uuid.UUID(str(cancelled["id"])))
            .values(status=InvoiceStatus.CANCELLED)
        )
        await session.commit()

    # This is the actual app role used by ASGI, rather than a SET ROLE
    # approximation.  Artifact RLS is therefore active during the service's
    # second query while Invoice ownership is checked explicitly.
    async with runtime_session_maker() as session:
        assert await session.scalar(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ) is False

    cases = (
        (uuid.uuid4(), 404, "ARTIFACT_UPLOAD_NOT_FOUND"),
        (uuid.UUID(str(foreign["id"])), 404, "ARTIFACT_UPLOAD_NOT_FOUND"),
        (uuid.UUID(str(draft["id"])), 404, "ARTIFACT_UPLOAD_NOT_FOUND"),
        (uuid.UUID(str(cancelled["id"])), 404, "ARTIFACT_UPLOAD_NOT_FOUND"),
        (uuid.UUID(str(existing["id"])), 409, "ARTIFACT_ALREADY_EXISTS"),
    )
    for invoice_id, expected_status, expected_code in cases:
        for suffix in ("artifacts", "artifacts/validate-upload?language=en"):
            received = 0

            async def body() -> AsyncIterator[bytes]:
                nonlocal received
                received += 1
                yield b"this multipart body must never be consumed"

            response = await db_client.post(
                f"/api/v1/invoices/{invoice_id}/{suffix}",
                headers={"content-type": "multipart/form-data; boundary=unread"},
                content=body(),
            )
            assert response.status_code == expected_status, response.text
            assert response.json()["detail"]["code"] == expected_code
            assert received == 0
