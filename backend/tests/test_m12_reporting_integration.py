"""M12 Step 8 report projections exercised through the runtime app role."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from test_m12_advance_integration import _additional_formal_quote, _tail_quote
from test_m12_correction_followup_integration import _issued_final
from test_m12_credit_integration import _issued_treatment_standard
from test_m12_final_integration import _issued_advance
from test_m12_refund_integration import _issue_full_credit
from test_quote_payment_integration import _full_auth, _setup_company

from jai.services.reporting import dashboard as dashboard_service

pytestmark = pytest.mark.integration


def _box(report: dict, name: str) -> tuple[Decimal, Decimal]:
    row = report["boxes"][name]
    return Decimal(row["base"]), Decimal(row.get("vat", "0"))


def _mixed_event_boxes(rows: list[dict]) -> dict[str, tuple[Decimal, Decimal]]:
    """Rebuild the invoice-side 21/9/0 boxes from public event-row metadata."""
    totals = {
        "box_1a": (Decimal(), Decimal()),
        "box_1b": (Decimal(), Decimal()),
        "box_1e": (Decimal(), Decimal()),
    }
    for row in rows:
        assert row["vat_treatment_effect"] == "APPLY_RATE"
        rate = Decimal(row["vat_rate_percent"])
        box = {
            Decimal("21"): "box_1a",
            Decimal("9"): "box_1b",
            Decimal("0"): "box_1e",
        }[rate]
        base, vat = totals[box]
        totals[box] = (
            base + Decimal(row["taxable_amount"]),
            vat + Decimal(row["vat_amount"]),
        )
    return totals


async def _vat(client: AsyncClient, quarter: int) -> dict:
    response = await client.get(f"/api/v1/reports/vat-return?year=2026&quarter={quarter}")
    assert response.status_code == 200, response.text
    return response.json()


async def _pl(client: AsyncClient, date_from: str, date_to: str) -> dict:
    response = await client.get(
        f"/api/v1/reports/profit-loss?from={date_from}&to={date_to}&granularity=quarter"
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_formal_50_50_cross_quarter_uses_invoice_events_not_cash_and_dashboard(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Advance/Final BTW is residual and invoice-dated; Final P/L is full net."""

    class _FrozenMayDatetime(datetime):
        @classmethod
        def now(cls, tz: object | None = None) -> _FrozenMayDatetime:
            return cls(2026, 5, 15, tzinfo=tz)

    monkeypatch.setattr(dashboard_service, "datetime", _FrozenMayDatetime)
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    # Step-3 helper dates the Advance 2026-02-01, so it is a Q1 tax event.
    q1_before_cash = await _vat(db_client, 1)
    assert _box(q1_before_cash, "box_1a") == (Decimal("50.00"), Decimal("10.50"))
    q1_event_refs = [
        (row["document_kind"], row["source_document_id"])
        for row in q1_before_cash["event_rows"]
    ]
    assert q1_event_refs == [
        ("ADVANCE", None)
    ]
    assert (await _pl(db_client, "2026-01-01", "2026-03-31"))["revenue_net"] == "0"

    payment = await db_client.post(
        f"/api/v1/invoices/{advance['id']}/payments",
        json={"payment_date": "2026-02-15", "amount": "20.00"},
    )
    assert payment.status_code == 201, payment.text
    # Formal cash has no tax snapshot and cannot change the frozen event.
    assert await _vat(db_client, 1) == q1_before_cash

    final = await _issued_final(db_client, quote["id"], invoice_date="2026-05-01")
    q2 = await _vat(db_client, 2)
    assert _box(q2, "box_1a") == (Decimal("50.00"), Decimal("10.50"))
    q2_events = [
        (row["document_kind"], row["taxable_amount"], row["vat_amount"])
        for row in q2["event_rows"]
    ]
    assert q2_events == [
        ("FINAL", "50.00", "10.50")
    ]
    assert (await _pl(db_client, "2026-04-01", "2026-06-30"))["revenue_net"] == "100.00"
    dashboard = await db_client.get("/api/v1/reports/dashboard?year=2026")
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["kpi"]["ytd_revenue"] == "100.00"
    assert (
        dashboard.json()["kpi"]["current_quarter_vat_payable"]
        == q2["totals"]["net_payable_or_refundable"]["vat"]
    )
    assert final["document_kind"] == "FINAL"


async def test_formal_50_50_same_quarter_projects_two_distinct_events(
    db_client: AsyncClient,
) -> None:
    """Same-quarter formal 50/50 still emits Advance plus Final residual once."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, quote["id"], "50")
    await _issued_final(db_client, quote["id"], invoice_date="2026-03-01")
    q1 = await _vat(db_client, 1)
    assert _box(q1, "box_1a") == (Decimal("100.00"), Decimal("21.00"))
    assert sorted(row["document_kind"] for row in q1["event_rows"]) == ["ADVANCE", "FINAL"]
    assert sum((Decimal(row["taxable_amount"]) for row in q1["event_rows"]), Decimal()) == Decimal(
        "100.00"
    )
    assert (await _pl(db_client, "2026-01-01", "2026-03-31"))["revenue_net"] == "100.00"


async def test_mixed_20_50_30_formal_project_reports_each_tax_bucket_once(
    db_client: AsyncClient,
) -> None:
    """All 21/9/0 Advance buckets are retained and a fully applied Final is VAT-zero."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _tail_quote(db_client, seeds, include_zero_bucket=True)
    advances = [
        await _issued_advance(db_client, quote["id"], percentage)
        for percentage in ("20", "50", "30")
    ]
    q1 = await _vat(db_client, 1)
    assert {row["document_kind"] for row in q1["event_rows"]} == {"ADVANCE"}
    assert {row["vat_treatment_code"] for row in q1["event_rows"]} == {"NL_DOMESTIC"}
    assert {Decimal(row["vat_rate_percent"]) for row in q1["event_rows"]} == {
        Decimal("21"), Decimal("9"), Decimal("0")
    }
    # Cent-tail rows expose the exact frozen routing inputs, allowing each
    # public event to be reconciled to 1a/1b/1e without amount-derived guesses.
    assert _mixed_event_boxes(q1["event_rows"]) == {
        "box_1a": _box(q1, "box_1a"),
        "box_1b": _box(q1, "box_1b"),
        "box_1e": (_box(q1, "box_1e")[0], Decimal()),
    }
    # The formal event rows are the authoritative per-bucket projection: their
    # signed totals equal the original frozen Quote total despite cent tails.
    q1_taxable = sum((Decimal(row["taxable_amount"]) for row in q1["event_rows"]), Decimal())
    q1_vat = sum((Decimal(row["vat_amount"]) for row in q1["event_rows"]), Decimal())
    assert q1_taxable == Decimal("0.10")
    assert q1_vat == Decimal("0.01")
    final = await _issued_final(db_client, quote["id"], invoice_date="2026-04-01")
    q2 = await _vat(db_client, 2)
    assert q2["event_rows"] == []
    assert _box(q2, "box_1a") == (Decimal("0"), Decimal("0"))
    assert (await _pl(db_client, "2026-04-01", "2026-06-30"))["revenue_net"] == "0.10"
    assert len(advances) == 3 and Decimal(final["payable_before_payments"]) == Decimal("0")


async def test_advance_credit_revenue_rule_is_frozen_before_or_after_final(
    db_client: AsyncClient,
) -> None:
    """Pre-Final Advance Credits defer P/L; post-Final Credits reverse it."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)

    pre_quote = await _additional_formal_quote(db_client, seeds)
    pre_advance = await _issued_advance(db_client, pre_quote["id"], "50")
    await _issue_full_credit(db_client, pre_advance["id"], invoice_date="2026-03-01")
    await _issued_final(db_client, pre_quote["id"], invoice_date="2026-04-01")
    # The pre-Final Credit has affects_revenue=false: it corrects VAT in Q1
    # but the Final re-establishes the then-current full project P/L in Q2.
    assert _box(await _vat(db_client, 1), "box_1a") == (Decimal("0.00"), Decimal("0.00"))
    assert (await _pl(db_client, "2026-01-01", "2026-03-31"))["revenue_net"] == "0"
    assert (await _pl(db_client, "2026-04-01", "2026-06-30"))["revenue_net"] == "100.00"

    post_quote = await _additional_formal_quote(db_client, seeds)
    post_advance = await _issued_advance(db_client, post_quote["id"], "50")
    await _issued_final(db_client, post_quote["id"], invoice_date="2026-04-01")
    await _issue_full_credit(db_client, post_advance["id"], invoice_date="2026-05-01")
    # This Advance Credit was issued after its Final, so the immutable issue
    # flag makes it a Q2 P/L reversal.  This report also contains the first
    # project's 100 Final event: 100 + 100 - 50 = 150.
    assert (await _pl(db_client, "2026-04-01", "2026-06-30"))["revenue_net"] == "150.00"
    q2 = await _vat(db_client, 2)
    assert sorted((row["document_kind"], row["taxable_amount"]) for row in q2["event_rows"]) == [
        ("CREDIT_NOTE", "-50.00"),
        ("FINAL", "100.00"),
        ("FINAL", "50.00"),
    ]


@pytest.mark.parametrize(
    ("treatment_code", "country_code", "box"),
    [
        ("EU_B2B_REVERSE", "DE", "box_3b"),
        ("EU_B2C", "DE", "box_1a"),
        ("EXPORT_NON_EU", "US", "box_3a"),
    ],
)
async def test_cross_border_credit_inherits_negative_tax_and_icp_source_event(
    db_client: AsyncClient, treatment_code: str, country_code: str, box: str
) -> None:
    """Every existing cross-border snapshot is corrected by the dated Credit."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    source = await _issued_treatment_standard(
        db_client,
        rate_id=seeds["rates"]["NL standard (21%)"]["id"],
        treatment_id=seeds["treatments"][treatment_code]["id"],
        country_code=country_code,
    )
    credit = await _issue_full_credit(db_client, source["id"], invoice_date="2026-04-02")
    q2 = await _vat(db_client, 2)
    assert len(q2["event_rows"]) == 1
    event = q2["event_rows"][0]
    assert event["document_kind"] == "CREDIT_NOTE"
    assert event["source_document_id"] == source["id"]
    assert event["source_document_kind"] == "STANDARD"
    assert Decimal(event["taxable_amount"]) == Decimal("-100.00")
    assert _box(q2, box)[0] == Decimal("-100.00")
    assert q2["correction_warnings"][0]["code"] == "CREDIT_CROSS_PERIOD"
    assert q2["correction_warnings"][0]["source"]["document_kind"] == "STANDARD"
    assert "filed" not in q2["correction_warnings"][0]["message"].lower()
    if treatment_code == "EU_B2B_REVERSE":
        icp = await db_client.get("/api/v1/reports/icp?year=2026&quarter=2")
        assert icp.status_code == 200, icp.text
        assert icp.json()["total_net"] == "-100.00"
        assert icp.json()["lines"][0]["source_documents"][0]["source_document_id"] == source["id"]
        assert icp.json()["lines"][0]["source_documents"][0]["source_document_kind"] == "STANDARD"
        assert icp.json()["correction_warnings"][0]["code"] == "CREDIT_CROSS_PERIOD"
        assert icp.json()["correction_warnings"][0]["source"]["document_kind"] == "STANDARD"
    assert credit["document_kind"] == "CREDIT_NOTE"


async def test_credit_events_expose_real_advance_and_final_source_kinds(
    db_client: AsyncClient,
) -> None:
    """The report contract preserves the ORM source kind for every formal Credit."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)

    advance_quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, advance_quote["id"], "50")
    await _issue_full_credit(db_client, advance["id"], invoice_date="2026-04-02")

    final_quote = await _additional_formal_quote(db_client, seeds)
    await _issued_advance(db_client, final_quote["id"], "50")
    final = await _issued_final(db_client, final_quote["id"], invoice_date="2026-03-01")
    await _issue_full_credit(db_client, final["id"], invoice_date="2026-04-02")

    q2 = await _vat(db_client, 2)
    assert {row["source_document_kind"] for row in q2["event_rows"]} == {"ADVANCE", "FINAL"}
    assert {warning["source"]["document_kind"] for warning in q2["correction_warnings"]} == {
        "ADVANCE",
        "FINAL",
    }


async def test_formal_cancellation_and_refund_leave_auditable_zero_and_no_cash_tax(
    db_client: AsyncClient,
) -> None:
    """A cancellation reaches zero through independent Credits; Refund is no BTW event."""
    await _full_auth(db_client)
    seeds = await _setup_company(db_client)
    quote = await _additional_formal_quote(db_client, seeds)
    advance = await _issued_advance(db_client, quote["id"], "50")
    await db_client.post(
        f"/api/v1/invoices/{advance['id']}/payments",
        json={"payment_date": "2026-02-05", "amount": "60.50"},
    )
    final = await _issued_final(db_client, quote["id"], invoice_date="2026-03-01")
    preview = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/cancellation/preview",
        json={"invoice_date": "2026-04-01"},
    )
    assert preview.status_code == 200, preview.text
    created = await db_client.post(
        f"/api/v1/quotes/{quote['id']}/cancellation/create-credit-drafts",
        json={"preview_token": preview.json()["preview_token"], "invoice_date": "2026-04-01"},
    )
    assert created.status_code == 201, created.text
    credits = created.json()["credit_notes"]
    assert {row["source_invoice_id"] for row in credits} == {advance["id"], final["id"]}
    issued: list[dict] = []
    for draft in credits:
        response = await db_client.post(
            f"/api/v1/invoices/{draft['id']}/status", json={"status": "SENT"}
        )
        assert response.status_code == 200, response.text
        issued.append(response.json())
    q2_before_refund = await _vat(db_client, 2)
    assert _box(q2_before_refund, "box_1a") == (Decimal("-100.00"), Decimal("-21.00"))
    assert (await _pl(db_client, "2026-01-01", "2026-12-31"))["revenue_net"] == "0.00"
    advance_credit = next(row for row in issued if row["source_invoice_id"] == advance["id"])
    refund = await db_client.post(
        f"/api/v1/credit-notes/{advance_credit['id']}/refunds",
        json={"payment_date": "2026-04-02", "amount": "60.50"},
    )
    assert refund.status_code == 201, refund.text
    assert await _vat(db_client, 2) == q2_before_refund
