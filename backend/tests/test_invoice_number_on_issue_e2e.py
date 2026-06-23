"""End-to-end regression for the "defer invoice numbering to issue time" fix.

Requires a running PostgreSQL instance (``pytest -m integration``).

This module is the **Step 5 closure regression** for
``docs/plan/fixes/invoice-number-on-issue.md``.  Unlike the per-step service
tests, every assertion here travels through the **real HTTP endpoints**
(``httpx.AsyncClient`` against the ASGI app), proving the user-visible contract
end to end:

- The reason the fix exists: deleting an un-issued draft must skip **no**
  invoice number.  Create draft A (number ``None``) → delete A → create draft B
  → issue B (``DRAFT -> SENT``) → B receives the *first* number, with no gap
  inherited from the deleted draft A.
- API-level regression of Step 3/4 rendering: a draft is returned with
  ``invoice_number is None`` from both GET-by-id and the list endpoint; after
  issuing it the same invoice carries a real number in both views.
- The quote→invoice conversion path also defers numbering: a converted invoice
  is an unnumbered DRAFT and only receives its number on issue.

These checks deliberately overlap with the focused tests added in earlier
steps; the point of this file is a single, named, black-box narrative that an
auditor (or future maintainer) can read top-to-bottom to confirm the bug can
never regress.
"""

from __future__ import annotations

import pyotp
import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Self-contained helpers (mirrors the other integration modules; no cross-file
# imports, so this regression stands on its own).
# ---------------------------------------------------------------------------


async def _full_auth(
    client: AsyncClient,
    email: str = "owner@example.com",
    password: str = "testpassword1",
) -> None:
    resp = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert resp.status_code == 201

    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200

    resp = await client.post("/api/v1/auth/mfa/setup")
    assert resp.status_code == 200
    secret = resp.json()["secret"]

    code = pyotp.TOTP(secret).now()
    resp = await client.post("/api/v1/auth/mfa/verify", json={"code": code})
    assert resp.status_code == 204


async def _setup_company(client: AsyncClient) -> dict:
    resp = await client.put(
        "/api/v1/company",
        json={"name": "Test Co", "base_currency": "EUR", "country_code": "NL"},
    )
    assert resp.status_code == 200

    rates_resp = await client.get("/api/v1/vat-rates")
    rates = {r["label"]: r for r in rates_resp.json()["items"]}
    return {"rates": rates}


async def _create_customer(client: AsyncClient, *, name: str = "Test Customer") -> str:
    resp = await client.post(
        "/api/v1/customers",
        json={
            "name": name,
            "addresses": [
                {"type": "BILLING", "country_code": "NL", "city": "Amsterdam"}
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _invoice_payload(customer_id: str, rate_id: str) -> dict:
    return {
        "customer_id": customer_id,
        "invoice_date": "2026-06-23",
        "tax_mode": "LINE",
        "amounts_include_vat": False,
        "lines": [
            {
                "name": "Service",
                "quantity": "1",
                "unit_price": "100.000",
                "vat_rate_id": rate_id,
            }
        ],
    }


async def _create_draft(
    client: AsyncClient, customer_id: str, rate_id: str
) -> dict:
    resp = await client.post(
        "/api/v1/invoices", json=_invoice_payload(customer_id, rate_id)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _issue(client: AsyncClient, invoice_id: str) -> dict:
    """Issue (DRAFT -> SENT); the legal number is allocated here, not at create."""
    resp = await client.post(
        f"/api/v1/invoices/{invoice_id}/status", json={"status": "SENT"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _next_sequence(client: AsyncClient) -> int:
    resp = await client.get("/api/v1/settings/invoice-number-sequence")
    assert resp.status_code == 200, resp.text
    return int(resp.json()["next_sequence"])


# ---------------------------------------------------------------------------
# Core regression: deleting a draft skips no number (the reason for this fix)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteDraftSkipsNoNumberE2E:

    async def test_delete_draft_then_issue_next_has_no_gap(
        self, db_client: AsyncClient
    ) -> None:
        """Full HTTP narrative proving the bug cannot recur.

        Create draft A → assert no number → DELETE A → create draft B → issue B
        → B receives INV-000001 (sequence 1).  Because A never consumed the
        company sequence, B inherits no gap from the deleted draft.
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # The company sequence starts at 1 and has not moved.
        assert await _next_sequence(db_client) == 1

        # Draft A: created with NO number (deferred to issue).
        draft_a = await _create_draft(db_client, customer_id, rate_21)
        assert draft_a["status"] == "DRAFT"
        assert draft_a["invoice_number"] is None
        assert draft_a["sequence_number"] is None

        # Creating a draft does not touch the sequence.
        assert await _next_sequence(db_client) == 1

        # Delete the never-issued draft A.
        del_resp = await db_client.delete(f"/api/v1/invoices/{draft_a['id']}")
        assert del_resp.status_code == 204
        assert (
            await db_client.get(f"/api/v1/invoices/{draft_a['id']}")
        ).status_code == 404

        # Deleting the draft left the sequence untouched: no number was burned.
        assert await _next_sequence(db_client) == 1

        # Draft B: also created without a number.
        draft_b = await _create_draft(db_client, customer_id, rate_21)
        assert draft_b["invoice_number"] is None
        assert draft_b["sequence_number"] is None

        # Issue B → it now receives the FIRST number, no gap from deleted A.
        issued_b = await _issue(db_client, draft_b["id"])
        assert issued_b["invoice_number"] == "INV-000001"
        assert issued_b["sequence_number"] == 1

        # The sequence advanced by exactly one (next is 2).
        assert await _next_sequence(db_client) == 2

    async def test_repeated_delete_drafts_never_advance_sequence(
        self, db_client: AsyncClient
    ) -> None:
        """Deleting several drafts in a row still burns nothing.

        Three drafts created and deleted, then a fresh draft is issued: it must
        still be INV-000001, regardless of how many discarded drafts came
        before it.
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        for _ in range(3):
            draft = await _create_draft(db_client, customer_id, rate_21)
            assert draft["invoice_number"] is None
            del_resp = await db_client.delete(f"/api/v1/invoices/{draft['id']}")
            assert del_resp.status_code == 204

        assert await _next_sequence(db_client) == 1

        survivor = await _create_draft(db_client, customer_id, rate_21)
        issued = await _issue(db_client, survivor["id"])
        assert issued["invoice_number"] == "INV-000001"
        assert issued["sequence_number"] == 1


# ---------------------------------------------------------------------------
# API-level rendering regression (Step 3/4): null number in GET / list,
# real number after issue.
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDraftNumberRenderingE2E:

    async def test_draft_number_null_in_get_and_list_then_set_on_issue(
        self, db_client: AsyncClient
    ) -> None:
        """A draft surfaces ``invoice_number is None`` via both read paths.

        GET-by-id and the list endpoint both return ``None`` for a draft (the
        frontend renders this as "Concept / 草稿"); after issuing, both views
        carry the real allocated number.
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        draft = await _create_draft(db_client, customer_id, rate_21)
        inv_id = draft["id"]

        # GET-by-id: draft has no number.
        get_resp = await db_client.get(f"/api/v1/invoices/{inv_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["invoice_number"] is None
        assert get_resp.json()["sequence_number"] is None

        # List endpoint: the same draft item carries a null number (no crash,
        # no fabricated value); list sort/search must tolerate it.
        list_resp = await db_client.get("/api/v1/invoices")
        assert list_resp.status_code == 200
        items = {item["id"]: item for item in list_resp.json()["items"]}
        assert inv_id in items
        assert items[inv_id]["invoice_number"] is None

        # Issue it → both read paths now expose the allocated number.
        issued = await _issue(db_client, inv_id)
        assert issued["invoice_number"] == "INV-000001"

        get_after = await db_client.get(f"/api/v1/invoices/{inv_id}")
        assert get_after.json()["invoice_number"] == "INV-000001"
        assert get_after.json()["sequence_number"] == 1

        list_after = await db_client.get("/api/v1/invoices")
        items_after = {item["id"]: item for item in list_after.json()["items"]}
        assert items_after[inv_id]["invoice_number"] == "INV-000001"

    async def test_list_with_mixed_draft_and_issued_tolerates_null(
        self, db_client: AsyncClient
    ) -> None:
        """A list holding both a numbered and an unnumbered invoice is fine.

        The list endpoint must not choke when one item has a number and another
        is still a null-numbered draft (null-safe sort/search, Step 4 §②).
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        issued_draft = await _create_draft(db_client, customer_id, rate_21)
        await _issue(db_client, issued_draft["id"])  # INV-000001
        unissued_draft = await _create_draft(db_client, customer_id, rate_21)

        list_resp = await db_client.get("/api/v1/invoices")
        assert list_resp.status_code == 200
        items = {item["id"]: item for item in list_resp.json()["items"]}
        assert items[issued_draft["id"]]["invoice_number"] == "INV-000001"
        assert items[unissued_draft["id"]]["invoice_number"] is None

        # Searching by the real number returns only the issued invoice; the
        # null-numbered draft is silently excluded (does not error).
        search_resp = await db_client.get("/api/v1/invoices?q=INV-000001")
        assert search_resp.status_code == 200
        assert search_resp.json()["total"] == 1
        assert search_resp.json()["items"][0]["id"] == issued_draft["id"]


# ---------------------------------------------------------------------------
# Quote → invoice conversion path also defers numbering (Step 2 §④, D2/D5).
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQuoteConvertDefersNumberingE2E:

    async def test_converted_invoice_is_unnumbered_until_issued(
        self, db_client: AsyncClient
    ) -> None:
        """A quote converted to an invoice yields an unnumbered DRAFT.

        Numbering is allocated only when that converted invoice is issued, just
        like a normally created draft.  This guards the second create path
        (``clone_quote_to_invoice``) at the HTTP level.
        """
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        customer_id = await _create_customer(db_client)
        rate_21 = seeds["rates"]["NL standard (21%)"]["id"]

        # Create + send a quote, then convert it.
        quote_resp = await db_client.post(
            "/api/v1/quotes",
            json={
                "customer_id": customer_id,
                "quote_date": "2026-06-23",
                "tax_mode": "LINE",
                "amounts_include_vat": False,
                "lines": [
                    {
                        "name": "Service",
                        "quantity": "1",
                        "unit_price": "100.000",
                        "vat_rate_id": rate_21,
                    }
                ],
            },
        )
        assert quote_resp.status_code == 201, quote_resp.text
        quote_id = quote_resp.json()["id"]

        send_resp = await db_client.post(
            f"/api/v1/quotes/{quote_id}/status", json={"status": "SENT"}
        )
        assert send_resp.status_code == 200, send_resp.text

        convert_resp = await db_client.post(f"/api/v1/quotes/{quote_id}/convert")
        assert convert_resp.status_code == 201, convert_resp.text
        converted = convert_resp.json()

        # Converted invoice is an unnumbered DRAFT (number deferred to issue).
        assert converted["status"] == "DRAFT"
        assert converted["invoice_number"] is None
        assert converted["sequence_number"] is None
        # Conversion did not consume the company sequence.
        assert await _next_sequence(db_client) == 1

        # Issuing the converted invoice allocates the first number.
        issued = await _issue(db_client, converted["id"])
        assert issued["invoice_number"] == "INV-000001"
        assert issued["sequence_number"] == 1
        assert await _next_sequence(db_client) == 2
