"""M12 Step 5 source-bound Credit Note integration coverage."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_m12_advance_integration import _additional_formal_quote
from test_m12_final_integration import _issued_advance
from test_quote_payment_integration import _create_customer, _full_auth, _setup_company

from jai.db import set_rls_company
from jai.schemas.invoice import CreditDraftCreate, CreditDraftUpdate
from jai.services import credit as credit_service

pytestmark = pytest.mark.integration


async def _issued_standard(client: AsyncClient, rate_id: str) -> dict[str, object]:
    customer_id = await _create_customer(client)
    created = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-02-01",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [
                {
                    "name": "Source service",
                    "quantity": "2",
                    "unit_price": "50",
                    "vat_rate_id": rate_id,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    issued = await client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _issued_mixed_standard(
    client: AsyncClient, rates: dict[str, dict[str, object]]
) -> dict[str, object]:
    customer_id = await _create_customer(client)
    created = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-02-01",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "discount": {"type": "PERCENTAGE", "value": "10"},
            "lines": [
                {
                    "name": "Standard",
                    "quantity": "3",
                    "unit_price": "10",
                    "vat_rate_id": rates["NL standard (21%)"]["id"],
                },
                {
                    "name": "Reduced",
                    "quantity": "2",
                    "unit_price": "10",
                    "vat_rate_id": rates["NL reduced (9%)"]["id"],
                },
                {
                    "name": "Zero",
                    "quantity": "1",
                    "unit_price": "10",
                    "vat_rate_id": rates["Zero (0%)"]["id"],
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    issued = await client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _issued_document_standard(client: AsyncClient, rate_id: str) -> dict[str, object]:
    customer_id = await _create_customer(client)
    created = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-02-01",
            "tax_mode": "DOCUMENT",
            "amounts_include_vat": False,
            "document_vat_rate_id": rate_id,
            "lines": [
                {"name": "Document one", "quantity": "1", "unit_price": "40"},
                {"name": "Document two", "quantity": "1", "unit_price": "60"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    issued = await client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _issued_treatment_document_standard(
    client: AsyncClient, *, rate_id: str, treatment_id: str, country_code: str
) -> dict[str, object]:
    customer_id = await _create_customer(client, country_code=country_code)
    created = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-02-01",
            "tax_mode": "DOCUMENT",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment_id,
            "document_vat_rate_id": rate_id,
            "lines": [{"name": "Treatment document source", "quantity": "1", "unit_price": "100"}],
        },
    )
    assert created.status_code == 201, created.text
    issued = await client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _issued_treatment_standard(
    client: AsyncClient, *, rate_id: str, treatment_id: str, country_code: str
) -> dict[str, object]:
    customer_id = await _create_customer(client, country_code=country_code)
    created = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-02-01",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "vat_treatment_id": treatment_id,
            "lines": [
                {
                    "name": "Treatment source",
                    "quantity": "1",
                    "unit_price": "100",
                    "vat_rate_id": rate_id,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    issued = await client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


def _credit_payload(
    *, quantity: str | None = None, gross: str | None = None, invoice_date: str = "2026-02-03"
) -> dict[str, object]:
    if quantity is None and gross is None:
        return {"full_remaining": True, "invoice_date": invoice_date}
    return {
        "full_remaining": False,
        "invoice_date": invoice_date,
        "lines": [
            {
                "source_basis_line_id": "BASIS",
                "input_mode": "QUANTITY" if quantity is not None else "GROSS_AMOUNT",
                **({"quantity": quantity} if quantity is not None else {"gross_amount": gross}),
            }
        ],
    }


async def test_standard_credit_quantity_then_remainder_and_source_aggregate(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    payment = await db_client.post(
        f"/api/v1/invoices/{source['id']}/payments",
        json={"payment_date": "2026-02-02", "amount": "10"},
    )
    assert payment.status_code == 201, payment.text
    preview = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes/calculate",
        json={"full_remaining": True},
    )
    assert preview.status_code == 200, preview.text
    basis_id = preview.json()["lines"][0]["source_basis_line_id"]
    partial = _credit_payload(quantity="1")
    partial["lines"][0]["source_basis_line_id"] = basis_id  # type: ignore[index]
    draft = await db_client.post(f"/api/v1/invoices/{source['id']}/credit-notes", json=partial)
    assert draft.status_code == 201, draft.text
    # DRAFT coverage never reserves any source basis.
    assert (await db_client.get(f"/api/v1/invoices/{source['id']}")).json()[
        "credit_status"
    ] == "NOT_CREDITED"
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["document_kind"] == "CREDIT_NOTE"
    assert issued.json()["source_invoice_id"] == source["id"]
    source_after = await db_client.get(f"/api/v1/invoices/{source['id']}")
    assert source_after.json()["credit_status"] == "PARTIALLY_CREDITED"
    assert Decimal(source_after.json()["credited_total"]) == Decimal("60.50")
    remainder = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    assert remainder.status_code == 201, remainder.text
    issued_remainder = await db_client.post(
        f"/api/v1/invoices/{remainder.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued_remainder.status_code == 200, issued_remainder.text
    source_final = await db_client.get(f"/api/v1/invoices/{source['id']}")
    assert source_final.json()["credit_status"] == "CREDITED"
    assert Decimal(source_final.json()["due_amount"]) == Decimal("0")


async def test_credit_draft_lifecycle_events_are_atomic_ordered_and_rls_isolated(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Credit create/update share the chain's append-only event transaction."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    source = await _issued_advance(db_client, quote["id"], "50")
    created = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    assert created.status_code == 201, created.text
    credit = created.json()
    updated = await db_client.put(
        f"/api/v1/credit-notes/{credit['id']}",
        json={
            "full_remaining": True,
            "invoice_date": "2026-02-04",
            "reference_number": "CREDIT-EVENT-UPDATE",
        },
    )
    assert updated.status_code == 200, updated.text

    source_chain = await db_client.get(f"/api/v1/invoices/{source['id']}/document-chain")
    credit_chain = await db_client.get(f"/api/v1/invoices/{credit['id']}/document-chain")
    assert source_chain.status_code == credit_chain.status_code == 200
    assert source_chain.json() == credit_chain.json()
    chain = source_chain.json()
    assert {node["id"] for node in chain["nodes"]} >= {source["id"], credit["id"]}
    assert (
        "INVOICE_TO_CREDIT_NOTE",
        source["id"],
        credit["id"],
    ) in {
        (relation["relation_type"], relation["from_node_id"], relation["to_node_id"])
        for relation in chain["relations"]
    }
    events = [event for event in chain["events"] if event["invoice_id"] == credit["id"]]
    assert [event["event_type"] for event in events] == ["INVOICE_CREATED", "INVOICE_UPDATED"]
    assert all(event["quote_id"] == quote["id"] for event in events)
    assert all(event["metadata"] == {"document_kind": "CREDIT_NOTE"} for event in events)
    assert events[0]["actor_user_id"] == events[1]["actor_user_id"]
    assert events[0]["actor_user_id"] is not None

    foreign_company_id = uuid.uuid4()
    async with admin_session_maker() as session:
        await session.execute(
            text("INSERT INTO company (id, name, base_currency) VALUES (:id, 'Foreign Co', 'EUR')"),
            {"id": foreign_company_id},
        )
        await session.commit()
    async with runtime_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        persisted = (
            await session.execute(
                text(
                    "SELECT company_id, quote_id, invoice_id, actor_user_id, event_type, "
                    "metadata_json "
                    "FROM document_chain_event WHERE invoice_id = :credit_id ORDER BY event_order"
                ),
                {"credit_id": credit["id"]},
            )
        ).all()
        assert persisted == [
            (
                uuid.UUID(seeds["company_id"]),
                uuid.UUID(quote["id"]),
                uuid.UUID(credit["id"]),
                uuid.UUID(events[0]["actor_user_id"]),
                "INVOICE_CREATED",
                {"document_kind": "CREDIT_NOTE"},
            ),
            (
                uuid.UUID(seeds["company_id"]),
                uuid.UUID(quote["id"]),
                uuid.UUID(credit["id"]),
                uuid.UUID(events[1]["actor_user_id"]),
                "INVOICE_UPDATED",
                {"document_kind": "CREDIT_NOTE"},
            ),
        ]
        await set_rls_company(session, foreign_company_id)
        assert await session.scalar(
            text("SELECT count(*) FROM document_chain_event WHERE invoice_id = :credit_id"),
            {"credit_id": credit["id"]},
        ) == 0


async def test_credit_draft_event_failures_roll_back_create_and_update(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error after event append cannot leave draft or event debris behind."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    original_loader = credit_service._load_invoice_read

    async def fail_after_event(*_: object, **__: object) -> object:
        raise RuntimeError("force response-load failure after lifecycle event")

    monkeypatch.setattr(credit_service, "_load_invoice_read", fail_after_event)
    async with runtime_session_maker() as session:
        with pytest.raises(RuntimeError, match="force response-load failure"):
            await credit_service.create_credit_draft(
                session,
                company_id=uuid.UUID(seeds["company_id"]),
                source_id=uuid.UUID(source["id"]),
                body=CreditDraftCreate(full_remaining=True, invoice_date="2026-02-03"),
                creator_id=None,
            )
        await set_rls_company(session, seeds["company_id"])
        assert (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM invoice WHERE document_kind = 'CREDIT_NOTE'), "
                    "(SELECT count(*) FROM invoice_correction), "
                    "(SELECT count(*) FROM invoice_correction_line), "
                    "(SELECT count(*) FROM document_chain_event "
                    " WHERE invoice_id IN "
                    "(SELECT id FROM invoice WHERE document_kind = 'CREDIT_NOTE'))"
                )
            )
        ).one() == (0, 0, 0, 0)

    monkeypatch.setattr(credit_service, "_load_invoice_read", original_loader)
    created = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    assert created.status_code == 201, created.text
    credit = created.json()
    monkeypatch.setattr(credit_service, "_load_invoice_read", fail_after_event)
    async with runtime_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        before = (
            await session.execute(
                text(
                    "SELECT invoice_date, reference_number, "
                    "(SELECT count(*) FROM invoice_correction_line l JOIN invoice_correction c "
                    " ON c.id = l.correction_id WHERE c.credit_note_id = :credit_id), "
                    "(SELECT array_agg(event_type ORDER BY event_order) FROM document_chain_event "
                    " WHERE invoice_id = :credit_id) FROM invoice WHERE id = :credit_id"
                ),
                {"credit_id": credit["id"]},
            )
        ).one()
        with pytest.raises(RuntimeError, match="force response-load failure"):
            await credit_service.update_credit_draft(
                session,
                company_id=uuid.UUID(seeds["company_id"]),
                credit_id=uuid.UUID(credit["id"]),
                body=CreditDraftUpdate(
                    full_remaining=True,
                    invoice_date="2026-02-04",
                    reference_number="MUST-ROLL-BACK",
                ),
                actor_user_id=None,
            )
        await set_rls_company(session, seeds["company_id"])
        after = (
            await session.execute(
                text(
                    "SELECT invoice_date, reference_number, "
                    "(SELECT count(*) FROM invoice_correction_line l JOIN invoice_correction c "
                    " ON c.id = l.correction_id WHERE c.credit_note_id = :credit_id), "
                    "(SELECT array_agg(event_type ORDER BY event_order) FROM document_chain_event "
                    " WHERE invoice_id = :credit_id) FROM invoice WHERE id = :credit_id"
                ),
                {"credit_id": credit["id"]},
            )
        ).one()
        assert after == before


async def test_credit_gross_rejects_over_credit_and_credit_of_credit(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    preview = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes/calculate", json={"full_remaining": True}
    )
    basis_id = preview.json()["lines"][0]["source_basis_line_id"]
    over = _credit_payload(gross="121.01")
    over["lines"][0]["source_basis_line_id"] = basis_id  # type: ignore[index]
    assert (
        await db_client.post(f"/api/v1/invoices/{source['id']}/credit-notes/calculate", json=over)
    ).status_code == 422
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    assert draft.status_code == 201, draft.text
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert (
        await db_client.post(
            f"/api/v1/invoices/{draft.json()['id']}/credit-notes/calculate",
            json={"full_remaining": True},
        )
    ).status_code == 422


async def test_credit_drafts_do_not_reserve_and_stale_loser_rolls_back(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    first = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    second = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    assert first.status_code == second.status_code == 201
    # Delete is legal only for DRAFT and releases no allocation because none
    # existed.  A second full draft is still unreserved until it is issued.
    assert (await db_client.delete(f"/api/v1/invoices/{first.json()['id']}")).status_code == 204
    replacement = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    assert replacement.status_code == 201
    assert (
        await db_client.post(
            f"/api/v1/invoices/{second.json()['id']}/status", json={"status": "SENT"}
        )
    ).status_code == 200
    stale = await db_client.post(
        f"/api/v1/invoices/{replacement.json()['id']}/status", json={"status": "SENT"}
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "CREDIT_STALE_BASIS"
    replacement_state = await db_client.get(f"/api/v1/invoices/{replacement.json()['id']}")
    assert replacement_state.json()["status"] == "DRAFT"


async def test_credit_draft_cancel_reactivate_never_reserves_or_numbers(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    assert draft.status_code == 201, draft.text
    cancelled = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "CANCELLED"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["invoice_number"] is None
    assert (await db_client.get(f"/api/v1/invoices/{source['id']}")).json()[
        "credit_status"
    ] == "NOT_CREDITED"
    restored = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "DRAFT"}
    )
    assert restored.status_code == 200, restored.text
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert (
        await db_client.post(
            f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "CANCELLED"}
        )
    ).status_code == 409


async def test_concurrent_credit_issue_has_one_number_event_and_aggregate(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Two full DRAFTs race; only one may consume the unreserved basis."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    drafts = await asyncio.gather(
        *[
            db_client.post(
                f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
            )
            for _ in range(2)
        ]
    )
    assert [response.status_code for response in drafts] == [201, 201]
    issued = await asyncio.gather(
        *[
            db_client.post(
                f"/api/v1/invoices/{response.json()['id']}/status", json={"status": "SENT"}
            )
            for response in drafts
        ]
    )
    assert sorted(response.status_code for response in issued) == [200, 409]
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        totals = (
            await session.execute(
                text(
                    "SELECT credited_total, "
                    "(SELECT count(*) FROM document_chain_event WHERE invoice_id IN "
                    " (SELECT credit_note_id FROM invoice_correction "
                    "  WHERE source_invoice_id = :source_id)), "
                    "(SELECT count(*) FROM invoice WHERE document_kind = 'CREDIT_NOTE' "
                    " AND status = 'SENT') FROM invoice WHERE id = :source_id"
                ),
                {"source_id": source["id"]},
            )
        ).one()
        assert Decimal(str(totals[0])) == Decimal("121")
        # Both legal unreserved DRAFT creates are auditable; only one later
        # issue succeeds, so the chain has two create events and one issue.
        assert totals[1:] == (3, 1)


async def test_runtime_rls_and_correction_trigger_reject_wrong_source(
    db_client: AsyncClient,
    runtime_session_maker: async_sessionmaker[AsyncSession],
    admin_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Raw correction writes obey FORCE RLS and the database ownership arbiter."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    other_source = await _issued_standard(
        db_client, seeds["rates"]["NL standard (21%)"]["id"]
    )
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    assert draft.status_code == 201, draft.text
    draft_source = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": source["customer_id"],
            "invoice_date": "2026-02-03",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [
                {
                    "name": "Unissued source",
                    "quantity": "1",
                    "unit_price": "1",
                    "vat_rate_id": seeds["rates"]["NL standard (21%)"]["id"],
                }
            ],
        },
    )
    assert draft_source.status_code == 201, draft_source.text
    unissued_source_id = draft_source.json()["id"]
    unissued_credit = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    assert unissued_credit.status_code == 201, unissued_credit.text

    # Seed a second tenant through the admin-only connection.  The runtime
    # role must neither see it nor write a correction into its company.
    foreign_company_id = uuid.uuid4()
    async with admin_session_maker() as session:
        await session.execute(
            text("INSERT INTO company (id, name, base_currency) VALUES (:id, 'Foreign Co', 'EUR')"),
            {"id": foreign_company_id},
        )
        await session.commit()

    quote_one = await _additional_formal_quote(db_client, seeds)
    advance_one = await _issued_advance(db_client, quote_one["id"], "20")
    quote_two = await _additional_formal_quote(db_client, seeds)
    advance_two = await _issued_advance(db_client, quote_two["id"], "20")
    formal_credit = await db_client.post(
        f"/api/v1/invoices/{advance_one['id']}/credit-notes", json=_credit_payload()
    )
    assert formal_credit.status_code == 201, formal_credit.text

    async with runtime_session_maker() as session:
        role = (
            await session.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        assert role == (False, False)
        rls = (
            await session.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname IN ('invoice_correction', 'invoice_correction_line') "
                    "ORDER BY relname"
                )
            )
        ).all()
        assert rls == [
            ("invoice_correction", True, True),
            ("invoice_correction_line", True, True),
        ]
        assert await session.scalar(text("SELECT count(*) FROM invoice_correction")) == 0
        await set_rls_company(session, seeds["company_id"])
        assert await session.scalar(text("SELECT count(*) FROM invoice_correction")) == 3
        other_basis_id = await session.scalar(
            text("SELECT id FROM invoice_credit_basis_line WHERE invoice_id = :invoice_id"),
            {"invoice_id": other_source["id"]},
        )
        assert other_basis_id is not None

        async def rejected(
            statement: str, params: dict[str, object], *, sqlstate: str
        ) -> None:
            await set_rls_company(session, seeds["company_id"])
            with pytest.raises(DBAPIError) as failure:
                await session.execute(text(statement), params)
            assert getattr(failure.value.orig, "sqlstate", None) == sqlstate
            await session.rollback()
            # A failed raw statement must not poison this runtime session.
            await set_rls_company(session, seeds["company_id"])
            assert await session.scalar(text("SELECT 1")) == 1

        # INSERT rejects a non-Credit document as the correction owner.
        await rejected(
            "INSERT INTO invoice_correction (company_id, credit_note_id, source_invoice_id) "
            "VALUES (:company_id, :source_id, :source_id)",
            {"company_id": seeds["company_id"], "source_id": source["id"]},
            sqlstate="23514",
        )
        # Remove this DRAFT's service-created row only to exercise the raw
        # INSERT arbiter with a valid Credit owner and an unissued source.
        await session.execute(
            text("DELETE FROM invoice_correction WHERE credit_note_id = :credit_id"),
            {"credit_id": unissued_credit.json()["id"]},
        )
        await session.commit()
        await rejected(
            "INSERT INTO invoice_correction (company_id, credit_note_id, source_invoice_id) "
            "VALUES (:company_id, :credit_id, :source_id)",
            {
                "company_id": seeds["company_id"],
                "credit_id": unissued_credit.json()["id"],
                "source_id": unissued_source_id,
            },
            sqlstate="23514",
        )
        await rejected(
            "UPDATE invoice_correction SET source_invoice_id = credit_note_id "
            "WHERE credit_note_id = :credit_id",
            {"credit_id": draft.json()["id"]},
            sqlstate="23514",
        )
        await rejected(
            "UPDATE invoice_correction SET source_invoice_id = :source_id "
            "WHERE credit_note_id = :credit_id",
            {"source_id": other_source["id"], "credit_id": draft.json()["id"]},
            sqlstate="23514",
        )
        # Both formal sources are issued but originate from different Quotes.
        await rejected(
            "UPDATE invoice_correction SET source_invoice_id = :source_id "
            "WHERE credit_note_id = :credit_id",
            {"source_id": advance_two["id"], "credit_id": formal_credit.json()["id"]},
            sqlstate="23514",
        )
        await rejected(
            "UPDATE invoice_correction_line SET source_basis_line_id = :basis_id "
            "WHERE correction_id = (SELECT id FROM invoice_correction "
            "WHERE credit_note_id = :credit_id)",
            {"basis_id": other_basis_id, "credit_id": draft.json()["id"]},
            sqlstate="23514",
        )
        await rejected(
            "UPDATE invoice_correction SET company_id = :company_id "
            "WHERE credit_note_id = :credit_id",
            {"company_id": foreign_company_id, "credit_id": draft.json()["id"]},
            # The ownership trigger fires before the policy's WITH CHECK;
            # either way no cross-company row is writable.  Keep the actual
            # deterministic trigger SQLSTATE as the regression contract.
            sqlstate="23514",
        )
        # Issue aggregates and affects_revenue are a single frozen fact: a
        # runtime role must never be able to park a partial-NULL state that
        # PostgreSQL would otherwise accept as CHECK UNKNOWN.
        for column, value in (
            ("issued_net_amount", Decimal("1")),
            ("issued_vat_amount", Decimal("1")),
            ("issued_gross_amount", Decimal("2")),
            ("issued_base_net_amount", Decimal("1")),
            ("issued_base_vat_amount", Decimal("1")),
            ("issued_base_gross_amount", Decimal("2")),
            ("affects_revenue", True),
        ):
            await rejected(
                f"UPDATE invoice_correction SET {column} = :value "
                "WHERE credit_note_id = :credit_id",
                {"value": value, "credit_id": draft.json()["id"]},
                sqlstate="23514",
            )
        await set_rls_company(session, foreign_company_id)
        assert await session.scalar(text("SELECT count(*) FROM invoice_correction")) == 0
        # Legal no-op writes are not rejected merely because the role is
        # runtime/NOBYPASSRLS: trigger and RLS agree on the same source.
        await set_rls_company(session, seeds["company_id"])
        await session.execute(
            text("UPDATE invoice_correction SET source_invoice_id = source_invoice_id "
                 "WHERE credit_note_id = :credit_id"),
            {"credit_id": draft.json()["id"]},
        )
        await session.execute(
            text("UPDATE invoice_correction_line SET sort_order = sort_order "
                 "WHERE correction_id = (SELECT id FROM invoice_correction "
                 "WHERE credit_note_id = :credit_id)"),
            {"credit_id": draft.json()["id"]},
        )
        await session.commit()
        await set_rls_company(session, seeds["company_id"])
        restored_source = await session.scalar(
            text("SELECT source_invoice_id FROM invoice_correction WHERE credit_note_id = :id"),
            {"id": draft.json()["id"]},
        )
        assert str(restored_source) == source["id"]


async def test_credit_draft_delete_cascades_correction_and_selection_rows(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=_credit_payload()
    )
    assert draft.status_code == 201, draft.text
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        correction_id = await session.scalar(
            text("SELECT id FROM invoice_correction WHERE credit_note_id = :id"),
            {"id": draft.json()["id"]},
        )
        assert correction_id is not None
        assert await session.scalar(
            text("SELECT count(*) FROM invoice_correction_line WHERE correction_id = :id"),
            {"id": correction_id},
        ) == 1
    assert (await db_client.delete(f"/api/v1/invoices/{draft.json()['id']}")).status_code == 204
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        assert await session.scalar(
            text("SELECT count(*) FROM invoice_correction WHERE id = :id"),
            {"id": correction_id},
        ) == 0
        assert await session.scalar(
            text("SELECT count(*) FROM invoice_correction_line WHERE correction_id = :id"),
            {"id": correction_id},
        ) == 0


async def test_credit_mixed_lines_quantity_gross_and_discount_keep_snapshot_totals(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_mixed_standard(db_client, seeds["rates"])
    calculated = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes/calculate", json={"full_remaining": True}
    )
    assert calculated.status_code == 200, calculated.text
    all_rows = calculated.json()["lines"]
    assert {Decimal(row["vat_rate_percent"]) for row in all_rows} == {
        Decimal("0"),
        Decimal("9"),
        Decimal("21"),
    }
    standard = next(row for row in all_rows if Decimal(row["vat_rate_percent"]) == Decimal("21"))
    reduced = next(row for row in all_rows if Decimal(row["vat_rate_percent"]) == Decimal("9"))
    partial = {
        "full_remaining": False,
        "lines": [
            {
                "source_basis_line_id": standard["source_basis_line_id"],
                "input_mode": "QUANTITY",
                "quantity": "1",
            },
            {
                "source_basis_line_id": reduced["source_basis_line_id"],
                "input_mode": "GROSS_AMOUNT",
                "gross_amount": "5.00",
            },
        ],
    }
    preview = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes/calculate", json=partial
    )
    assert preview.status_code == 200, preview.text
    preview_lines = preview.json()["lines"]
    preview_total = sum(
        (Decimal(row["net_amount"]) + Decimal(row["vat_amount"]) for row in preview_lines),
        Decimal("0"),
    )
    assert preview_total == Decimal(preview.json()["gross_amount"])
    draft_payload = {**partial, "invoice_date": "2026-02-03"}
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes", json=draft_payload
    )
    assert draft.status_code == 201, draft.text
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["vat_treatment_snapshot"] == source["vat_treatment_snapshot"]
    assert Decimal(issued.json()["total_incl_vat"]) == Decimal(preview.json()["gross_amount"])


async def test_credit_document_tax_source_uses_frozen_document_snapshot(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_document_standard(
        db_client, seeds["rates"]["NL standard (21%)"]["id"]
    )
    preview = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes/calculate", json={"full_remaining": True}
    )
    assert preview.status_code == 200, preview.text
    assert Decimal(preview.json()["net_amount"]) == Decimal("100")
    assert Decimal(preview.json()["vat_amount"]) == Decimal("21")
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-03"},
    )
    assert draft.status_code == 201, draft.text
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["tax_mode"] == "DOCUMENT"
    assert len(issued.json()["taxes"]) == 1
    assert Decimal(issued.json()["taxes"][0]["tax_amount"]) == Decimal("21")


async def test_credit_numbering_custom_start_is_independent_from_invoice_series(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    config = await db_client.put(
        "/api/v1/settings/credit-numbering",
        json={"template": "{{SERIES:CR}}-{{SEQUENCE:3}}", "sequence_start": 50},
    )
    assert config.status_code == 200, config.text
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    credit = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-03"},
    )
    assert credit.status_code == 201, credit.text
    issued = await db_client.post(
        f"/api/v1/invoices/{credit.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["invoice_number"] == "CR-050"
    assert issued.json()["sequence_number"] == 50
    invoice_next = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    assert invoice_next["invoice_number"] != "CR-050"


async def test_credit_issue_skips_occupied_invoice_number_without_invoice_counter_drift(
    db_client: AsyncClient,
) -> None:
    """A real issue retries an occupied shared namespace candidate atomically."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    invoice_before = await db_client.get("/api/v1/settings/invoice-number-sequence")
    assert invoice_before.status_code == 200, invoice_before.text
    configured = await db_client.put(
        "/api/v1/settings/credit-numbering",
        json={"template": "{{SERIES:INV}}-{{SEQUENCE:6}}", "sequence_start": 1},
    )
    assert configured.status_code == 200, configured.text
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-03"},
    )
    assert draft.status_code == 201, draft.text
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    # INV-000001 is the source; the Credit allocator skips it and commits 2.
    assert issued.json()["invoice_number"] == "INV-000002"
    assert issued.json()["sequence_number"] == 2
    invoice_after = await db_client.get("/api/v1/settings/invoice-number-sequence")
    assert invoice_after.json() == invoice_before.json()


async def test_credit_issue_exhaustion_rolls_back_draft_and_sequence_state(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """An occupied final candidate fails atomically without consuming Credit state."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    maximum = 9_223_372_036_854_775_807
    number = f"MAX-{maximum}"
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        await session.execute(
            text("UPDATE invoice SET invoice_number = :number WHERE id = :id"),
            {"number": number, "id": source["id"]},
        )
        await session.commit()
    assert (
        await db_client.put(
            "/api/v1/settings/credit-numbering",
            json={"template": "{{SERIES:MAX}}-{{SEQUENCE:1}}", "sequence_start": maximum},
        )
    ).status_code == 200
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-03"},
    )
    assert draft.status_code == 201, draft.text
    draft_before = draft.json()
    source_before = await db_client.get(f"/api/v1/invoices/{source['id']}")
    assert source_before.status_code == 200, source_before.text
    failed = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert failed.status_code == 409, failed.text
    assert failed.json()["detail"]["code"] == "NUMBER_SEQUENCE_EXHAUSTED"
    after = await db_client.get(f"/api/v1/invoices/{draft.json()['id']}")
    assert after.json()["status"] == "DRAFT"
    assert after.json()["invoice_number"] is None
    source_after = await db_client.get(f"/api/v1/invoices/{source['id']}")
    assert source_after.json()["status"] == source_before.json()["status"]
    assert source_after.json()["credited_total"] == source_before.json()["credited_total"]
    assert source_after.json()["total_incl_vat"] == source_before.json()["total_incl_vat"]
    assert after.json()["invoice_date"] == draft_before["invoice_date"]
    assert after.json()["total_incl_vat"] == draft_before["total_incl_vat"]
    sequence = await db_client.get("/api/v1/settings/credit-number-sequence")
    assert sequence.json()["next_sequence"] == maximum
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        assert (
            await session.execute(
                text(
                    "SELECT issued_net_amount, issued_vat_amount, issued_gross_amount, "
                    "issued_base_net_amount, issued_base_vat_amount, issued_base_gross_amount, "
                    "affects_revenue "
                    "FROM invoice_correction WHERE credit_note_id = :id"
                ),
                {"id": draft.json()["id"]},
            )
        ).one() == (None, None, None, None, None, None, None)
        assert await session.scalar(
            text(
                "SELECT count(*) FROM document_chain_event "
                "WHERE invoice_id = :id AND event_type = 'INVOICE_ISSUED'"
            ),
            {"id": draft.json()["id"]},
        ) == 0
        assert await session.scalar(
            text(
                "SELECT count(*) FROM number_sequence "
                "WHERE company_id = :company_id AND document_type = 'CREDIT_NOTE'"
            ),
            {"company_id": seeds["company_id"]},
        ) == 0
    # The failed issue left the request/session usable: editing the DRAFT is
    # still a legal, atomic command even though its configured issue series
    # remains exhausted.
    reusable = await db_client.put(
        f"/api/v1/credit-notes/{draft.json()['id']}",
        json={"full_remaining": True, "invoice_date": "2026-02-04"},
    )
    assert reusable.status_code == 200, reusable.text
    assert reusable.json()["status"] == "DRAFT"
    assert reusable.json()["invoice_number"] is None


async def test_gross_credit_allocation_preserves_three_decimal_quantity(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    created = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-02-01",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [
                {
                    "name": "Three-decimal quantity",
                    "quantity": "1.005",
                    "unit_price": "100",
                    "vat_rate_id": seeds["rates"]["NL standard (21%)"]["id"],
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    source_response = await db_client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert source_response.status_code == 200, source_response.text
    source = source_response.json()
    calculation = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes/calculate", json={"full_remaining": True}
    )
    basis_id = calculation.json()["lines"][0]["source_basis_line_id"]
    first = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={
            "full_remaining": False,
            "invoice_date": "2026-02-03",
            "lines": [
                {
                    "source_basis_line_id": basis_id,
                    "input_mode": "GROSS_AMOUNT",
                    "gross_amount": "50.00",
                }
            ],
        },
    )
    assert first.status_code == 201, first.text
    first_issued = await db_client.post(
        f"/api/v1/invoices/{first.json()['id']}/status", json={"status": "SENT"}
    )
    assert first_issued.status_code == 200, first_issued.text
    assert Decimal(first_issued.json()["lines"][0]["quantity"]) == Decimal("0.413")
    second = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-04"},
    )
    assert second.status_code == 201, second.text
    second_issued = await db_client.post(
        f"/api/v1/invoices/{second.json()['id']}/status", json={"status": "SENT"}
    )
    assert second_issued.status_code == 200, second_issued.text
    assert Decimal(second_issued.json()["lines"][0]["quantity"]) == Decimal("0.592")
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        credited = (
            await session.execute(
            text(
                "SELECT sum(l.quantity), sum(l.net_amount), sum(l.vat_amount), "
                "sum(l.gross_amount), sum(l.base_net_amount), sum(l.base_vat_amount), "
                "sum(l.base_gross_amount) FROM invoice_correction_line l "
                "JOIN invoice_correction c ON c.id = l.correction_id "
                "JOIN invoice i ON i.id = c.credit_note_id "
                "WHERE c.source_invoice_id = :source_id AND i.status = 'SENT'"
            ),
            {"source_id": source["id"]},
        )
        ).one()
        expected = (
            Decimal("1.005"),
            Decimal(source["taxable_amount"]),
            Decimal(source["vat_total"]),
            Decimal(source["total_incl_vat"]),
            Decimal(source["base_taxable_amount"]),
            Decimal(source["base_vat_total"]),
            Decimal(source["base_total_incl_vat"]),
        )
        assert tuple(Decimal(str(value)) for value in credited) == expected


async def test_partial_gross_credit_keeps_single_currency_base_parity_and_remainder(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """A gross tail uses one closure for transaction and same-currency base."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    customer_id = await _create_customer(db_client)
    created = await db_client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": "2026-02-01",
            "tax_mode": "LINE",
            "amounts_include_vat": False,
            "lines": [
                {
                    "name": "Rounding tail",
                    "quantity": "1",
                    "unit_price": "1.01",
                    "vat_rate_id": seeds["rates"]["NL standard (21%)"]["id"],
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    source_response = await db_client.post(
        f"/api/v1/invoices/{created.json()['id']}/status", json={"status": "SENT"}
    )
    assert source_response.status_code == 200, source_response.text
    source = source_response.json()
    calculation = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes/calculate", json={"full_remaining": True}
    )
    basis_id = calculation.json()["lines"][0]["source_basis_line_id"]
    partial_payload = {
        "full_remaining": False,
        "lines": [
            {
                "source_basis_line_id": basis_id,
                "input_mode": "GROSS_AMOUNT",
                "gross_amount": "0.61",
            }
        ],
    }
    preview = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes/calculate", json=partial_payload
    )
    assert preview.status_code == 200, preview.text
    row = preview.json()["lines"][0]
    assert Decimal(row["net_amount"]) + Decimal(row["vat_amount"]) == Decimal("0.61")
    assert (
        Decimal(row["net_amount"]), Decimal(row["vat_amount"]), Decimal(row["gross_amount"])
    ) == (
        Decimal(row["base_net_amount"]),
        Decimal(row["base_vat_amount"]),
        Decimal(row["base_gross_amount"]),
    )
    first = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={**partial_payload, "invoice_date": "2026-02-03"},
    )
    assert first.status_code == 201, first.text
    first_issued = await db_client.post(
        f"/api/v1/invoices/{first.json()['id']}/status", json={"status": "SENT"}
    )
    assert first_issued.status_code == 200, first_issued.text
    remainder = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-04"},
    )
    assert remainder.status_code == 201, remainder.text
    remainder_issued = await db_client.post(
        f"/api/v1/invoices/{remainder.json()['id']}/status", json={"status": "SENT"}
    )
    assert remainder_issued.status_code == 200, remainder_issued.text
    source_after = await db_client.get(f"/api/v1/invoices/{source['id']}")
    assert source_after.status_code == 200, source_after.text
    assert Decimal(source_after.json()["credited_total"]) == Decimal(source["total_incl_vat"])
    assert Decimal(source_after.json()["base_credited_total"]) == Decimal(
        source["base_total_incl_vat"]
    )
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        totals = (
            await session.execute(
                text(
                    "SELECT sum(net_amount), sum(vat_amount), sum(gross_amount), "
                    "sum(base_net_amount), sum(base_vat_amount), sum(base_gross_amount) "
                    "FROM invoice_correction_line l JOIN invoice_correction c "
                    "ON c.id = l.correction_id "
                    "JOIN invoice i ON i.id = c.credit_note_id "
                    "WHERE c.source_invoice_id = :source_id AND i.status = 'SENT'"
                ),
                {"source_id": source["id"]},
            )
        ).one()
    values = tuple(Decimal(str(value)) for value in totals)
    assert values[:3] == values[3:]
    assert values[0] + values[1] == values[2]


async def test_credit_read_list_chain_and_legacy_reports_remain_bounded_and_isolated(
    db_client: AsyncClient,
) -> None:
    """Step 5 exposes Credit identity without making it an M10 report event."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    payment = await db_client.post(
        f"/api/v1/invoices/{source['id']}/payments",
        json={"payment_date": "2026-02-02", "amount": "10"},
    )
    assert payment.status_code == 201, payment.text
    baseline = await asyncio.gather(
        db_client.get("/api/v1/reports/vat-return?year=2026&quarter=1"),
        db_client.get("/api/v1/reports/icp?year=2026&quarter=1"),
        db_client.get("/api/v1/reports/profit-loss?from=2026-01-01&to=2026-03-31"),
        db_client.get("/api/v1/reports/dashboard?year=2026"),
    )
    assert all(response.status_code == 200 for response in baseline)
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-03"},
    )
    assert draft.status_code == 201, draft.text
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert (await db_client.get(f"/api/v1/invoices/{issued.json()['id']}")).json()[
        "source_invoice_id"
    ] == source["id"]
    listed = await db_client.get("/api/v1/invoices?document_kind=CREDIT_NOTE")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [issued.json()["id"]]
    chain = await db_client.get(f"/api/v1/invoices/{issued.json()['id']}/document-chain")
    assert chain.status_code == 200, chain.text
    chain_body = chain.json()
    source_chain = await db_client.get(f"/api/v1/invoices/{source['id']}/document-chain")
    assert source_chain.status_code == 200, source_chain.text
    assert source_chain.json() == chain_body
    assert {node["id"] for node in chain_body["nodes"]} >= {
        source["id"],
        issued.json()["id"],
    }
    assert {
        (relation["relation_type"], relation["from_node_id"], relation["to_node_id"])
        for relation in chain_body["relations"]
    } >= {("INVOICE_TO_CREDIT_NOTE", source["id"], issued.json()["id"])}
    assert any(
        event["invoice_id"] == issued.json()["id"] and event["event_type"] == "INVOICE_ISSUED"
        for event in chain_body["events"]
    )
    assert Decimal(chain_body["totals"]["credit_total"]) == Decimal("121")
    assert any(
        relation["relation_type"] == "INVOICE_TO_PAYMENT"
        and relation["from_node_id"] == source["id"]
        for relation in chain_body["relations"]
    )
    after = await asyncio.gather(
        db_client.get("/api/v1/reports/vat-return?year=2026&quarter=1"),
        db_client.get("/api/v1/reports/icp?year=2026&quarter=1"),
        db_client.get("/api/v1/reports/profit-loss?from=2026-01-01&to=2026-03-31"),
        db_client.get("/api/v1/reports/dashboard?year=2026"),
    )
    assert [response.json() for response in after] == [response.json() for response in baseline]


@pytest.mark.parametrize(
    ("treatment_code", "country_code"),
    [("EU_B2B_REVERSE", "DE"), ("EU_B2C", "DE"), ("EXPORT_NON_EU", "US")],
)
async def test_credit_inherits_cross_border_source_treatment_snapshot(
    db_client: AsyncClient, treatment_code: str, country_code: str
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_treatment_standard(
        db_client,
        rate_id=seeds["rates"]["NL standard (21%)"]["id"],
        treatment_id=seeds["treatments"][treatment_code]["id"],
        country_code=country_code,
    )
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-03"},
    )
    assert draft.status_code == 201, draft.text
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["vat_treatment_snapshot"] == source["vat_treatment_snapshot"]


@pytest.mark.parametrize("tax_mode", ["LINE", "DOCUMENT"])
async def test_credit_inherits_frozen_cross_border_effective_vat_after_master_mutation(
    db_client: AsyncClient, tax_mode: str
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    rate = seeds["rates"]["NL standard (21%)"]
    treatment = seeds["treatments"]["EU_B2B_REVERSE"]
    source = (
        await _issued_treatment_standard(
            db_client, rate_id=rate["id"], treatment_id=treatment["id"], country_code="DE"
        )
        if tax_mode == "LINE"
        else await _issued_treatment_document_standard(
            db_client, rate_id=rate["id"], treatment_id=treatment["id"], country_code="DE"
        )
    )
    source_tax = source["lines"][0]["line_taxes"][0] if tax_mode == "LINE" else source["taxes"][0]
    assert Decimal(source_tax["vat_rate_percent"]) == Decimal("21")
    assert Decimal(source_tax["effective_vat_percent"]) == Decimal("0")
    changed_rate = await db_client.put(
        f"/api/v1/vat-rates/{rate['id']}",
        json={"label": "Changed live nominal", "percent": "17", "active": True},
    )
    assert changed_rate.status_code == 200, changed_rate.text
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-03"},
    )
    assert draft.status_code == 201, draft.text
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    credit_tax = (
        issued.json()["lines"][0]["line_taxes"][0]
        if tax_mode == "LINE"
        else issued.json()["taxes"][0]
    )
    assert Decimal(credit_tax["vat_rate_percent"]) == Decimal("21")
    assert Decimal(credit_tax["effective_vat_percent"]) == Decimal("0")
    assert Decimal(credit_tax["tax_amount"]) == Decimal("0")
    assert issued.json()["vat_treatment_snapshot"] == source["vat_treatment_snapshot"]


async def test_credit_dates_update_identity_and_paid_source_settlement_are_guarded(
    db_client: AsyncClient,
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    paid = await db_client.post(
        f"/api/v1/invoices/{source['id']}/payments",
        json={"payment_date": "2026-02-02", "amount": "121.00"},
    )
    assert paid.status_code == 201, paid.text
    bad_date = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-01-31"},
    )
    assert bad_date.status_code == 422
    draft = await db_client.post(
        f"/api/v1/invoices/{source['id']}/credit-notes",
        json={"full_remaining": True, "invoice_date": "2026-02-03"},
    )
    assert draft.status_code == 201, draft.text
    update = await db_client.put(
        f"/api/v1/credit-notes/{draft.json()['id']}",
        json={
            "full_remaining": True,
            "invoice_date": "2026-02-04",
            "reference_number": "CREDIT-EDIT",
        },
    )
    assert update.status_code == 200, update.text
    assert update.json()["customer_id"] == source["customer_id"]
    assert update.json()["currency"] == source["currency"]
    issued = await db_client.post(
        f"/api/v1/invoices/{draft.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued.status_code == 200, issued.text
    state = (await db_client.get(f"/api/v1/invoices/{source['id']}")).json()
    assert state["paid_status"] == "PAID"  # cash is never relabelled as a Credit
    assert Decimal(state["credited_total"]) == Decimal("121.00")
    assert Decimal(state["due_amount"]) == Decimal("0")
    assert Decimal(state["refund_due_amount"]) == Decimal("121.00")
    assert Decimal(state["base_refund_due_amount"]) == Decimal("121.00")
    assert state["settlement_status"] == "REFUND_DUE"
    payment_id = paid.json()["items"][0]["id"]
    metadata_edit = await db_client.put(
        f"/api/v1/payments/{payment_id}",
        json={"payment_date": "2026-02-02", "amount": "121.00", "note": "corrected note"},
    )
    assert metadata_edit.status_code == 200, metadata_edit.text
    metadata_state = await db_client.get(f"/api/v1/invoices/{source['id']}")
    assert metadata_state.status_code == 200, metadata_state.text
    assert metadata_state.json()["refund_due_amount"] == "121.000"
    rejected_increase = await db_client.put(
        f"/api/v1/payments/{payment_id}",
        json={"payment_date": "2026-02-02", "amount": "121.01", "note": "must reject"},
    )
    assert rejected_increase.status_code == 422
    decrease = await db_client.put(
        f"/api/v1/payments/{payment_id}",
        json={"payment_date": "2026-02-02", "amount": "120.00", "note": "smaller cash"},
    )
    assert decrease.status_code == 200, decrease.text
    decrease_state = await db_client.get(f"/api/v1/invoices/{source['id']}")
    assert decrease_state.status_code == 200, decrease_state.text
    assert decrease_state.json()["refund_due_amount"] == "120.000"
    deleted = await db_client.delete(f"/api/v1/payments/{payment_id}")
    assert deleted.status_code == 200, deleted.text
    deleted_state = await db_client.get(f"/api/v1/invoices/{source['id']}")
    assert deleted_state.status_code == 200, deleted_state.text
    assert deleted_state.json()["refund_due_amount"] == "0.000"


async def test_advance_and_final_credit_sources_and_final_draft_freeze(
    db_client: AsyncClient,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    final_draft = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final_draft.status_code == 201, final_draft.text
    assert (
        await db_client.post(
            f"/api/v1/invoices/{advance['id']}/credit-notes", json=_credit_payload()
        )
    ).status_code == 409
    assert (
        await db_client.delete(f"/api/v1/invoices/{final_draft.json()['id']}")
    ).status_code == 204
    advance_credit = await db_client.post(
        f"/api/v1/invoices/{advance['id']}/credit-notes", json=_credit_payload()
    )
    assert advance_credit.status_code == 201, advance_credit.text
    advance_credit_issue = await db_client.post(
        f"/api/v1/invoices/{advance_credit.json()['id']}/status", json={"status": "SENT"}
    )
    assert advance_credit_issue.status_code == 200, advance_credit_issue.text
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        assert await session.scalar(
            text(
                "SELECT affects_revenue FROM invoice_correction "
                "WHERE credit_note_id = :credit_id"
            ),
            {"credit_id": advance_credit.json()["id"]},
        ) is False
    post_final_advance = await _issued_advance(db_client, quote["id"], "10")
    final = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/final-invoice", json={"invoice_date": "2026-03-01"}
    )
    assert final.status_code == 201, final.text
    issued_final = await db_client.post(
        f"/api/v1/invoices/{final.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued_final.status_code == 200, issued_final.text
    post_final_advance_credit = await db_client.post(
        f"/api/v1/invoices/{post_final_advance['id']}/credit-notes",
        json=_credit_payload(invoice_date="2026-03-02"),
    )
    assert post_final_advance_credit.status_code == 201, post_final_advance_credit.text
    assert (
        await db_client.post(
            f"/api/v1/invoices/{post_final_advance_credit.json()['id']}/status",
            json={"status": "SENT"},
        )
    ).status_code == 200
    final_credit = await db_client.post(
        f"/api/v1/invoices/{final.json()['id']}/credit-notes",
        json=_credit_payload(invoice_date="2026-03-02"),
    )
    assert final_credit.status_code == 201, final_credit.text
    issued_final_credit = await db_client.post(
        f"/api/v1/invoices/{final_credit.json()['id']}/status", json={"status": "SENT"}
    )
    assert issued_final_credit.status_code == 200, issued_final_credit.text
    assert Decimal(issued_final_credit.json()["total_incl_vat"]) == Decimal(
        issued_final.json()["payable_before_payments"]
    )
    assert Decimal(issued_final_credit.json()["total_incl_vat"]) != Decimal(
        issued_final.json()["total_incl_vat"]
    )
    final_after_credit = await db_client.get(f"/api/v1/invoices/{final.json()['id']}")
    assert final_after_credit.status_code == 200, final_after_credit.text
    assert Decimal(final_after_credit.json()["credited_total"]) == Decimal(
        issued_final.json()["payable_before_payments"]
    )
    assert final_after_credit.json()["credit_status"] == "CREDITED"
    assert final_after_credit.json()["final_advance_applications"] == issued_final.json()[
        "final_advance_applications"
    ]
    assert final_after_credit.json()["final_totals"] == issued_final.json()["final_totals"]
    formal_chain = await db_client.get(f"/api/v1/invoices/{final.json()['id']}/document-chain")
    assert formal_chain.status_code == 200, formal_chain.text
    assert (
        "INVOICE_TO_CREDIT_NOTE",
        final.json()["id"],
        final_credit.json()["id"],
    ) in {
        (relation["relation_type"], relation["from_node_id"], relation["to_node_id"])
        for relation in formal_chain.json()["relations"]
    }
    async with db_session_maker() as session:
        await set_rls_company(session, seeds["company_id"])
        # Later Final and Credit actions do not reinterpret the pre-Final
        # event: affects_revenue is an issue-time fact, not a live projection.
        assert await session.scalar(
            text(
                "SELECT affects_revenue FROM invoice_correction "
                "WHERE credit_note_id = :credit_id"
            ),
            {"credit_id": advance_credit.json()["id"]},
        ) is False
        assert await session.scalar(
            text(
                "SELECT affects_revenue FROM invoice_correction "
                "WHERE credit_note_id = :credit_id"
            ),
            {"credit_id": post_final_advance_credit.json()["id"]},
        ) is True
        assert await session.scalar(
            text(
                "SELECT affects_revenue FROM invoice_correction "
                "WHERE credit_note_id = :credit_id"
            ),
            {"credit_id": final_credit.json()["id"]},
        ) is True
