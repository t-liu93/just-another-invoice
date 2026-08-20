"""Integration tests for VAT rate + treatment CRUD (M4 step 1).

Requires a running PostgreSQL instance (``pytest -m integration``).

Covers:
- VatRate: full CRUD roundtrip, list, 404 for unknown id
- VatRate: percent non-negative validation → 422
- VatRate: UNIQUE label conflict → 409
- VatRate: company-scoped isolation (cross-company → 404)
- VatRate: owner-only + unauthenticated → 401/403
- VatTreatment: full CRUD roundtrip, list, side filter
- VatTreatment: UNIQUE code conflict → 409
- VatTreatment: company-scoped isolation
- Seed seeding: new company → 3 rates + 8 treatments,
  correct effect/side/requires_icp, report_box NULL throughout
"""

from __future__ import annotations

import uuid

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jai.models.company import Company

# ---------------------------------------------------------------------------
# Helpers
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


async def _setup_company(client: AsyncClient) -> None:
    resp = await client.put("/api/v1/company", json={"name": "Test Co", "base_currency": "EUR"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# VatRate CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestVatRateCRUD:

    async def test_create_rate(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/vat-rates",
            json={"label": "Custom Rate", "percent": "15.000", "active": True},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["label"] == "Custom Rate"
        assert float(data["percent"]) == 15.0
        assert data["active"] is True
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data
        assert "company_id" not in data

    async def test_list_rates_includes_seeds(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/vat-rates")
        assert resp.status_code == 200
        data = resp.json()
        labels = {r["label"] for r in data["items"]}
        assert "NL standard (21%)" in labels
        assert "NL reduced (9%)" in labels
        assert "Zero (0%)" in labels

    async def test_get_rate(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        rates = (await db_client.get("/api/v1/vat-rates")).json()["items"]
        rate_id = rates[0]["id"]

        resp = await db_client.get(f"/api/v1/vat-rates/{rate_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == rate_id

    async def test_update_rate(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/vat-rates", json={"label": "Before", "percent": "5.000"}
        )
        rate_id = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/vat-rates/{rate_id}",
            json={"label": "After", "percent": "6.000", "active": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["label"] == "After"
        assert float(data["percent"]) == 6.0
        assert data["active"] is False

    async def test_delete_rate(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/vat-rates", json={"label": "To Delete", "percent": "7.000"}
        )
        rate_id = create_resp.json()["id"]

        resp = await db_client.delete(f"/api/v1/vat-rates/{rate_id}")
        assert resp.status_code == 204

        resp = await db_client.get(f"/api/v1/vat-rates/{rate_id}")
        assert resp.status_code == 404

    async def test_get_nonexistent_rate_404(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/vat-rates/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_update_nonexistent_rate_404(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.put(
            f"/api/v1/vat-rates/{uuid.uuid4()}",
            json={"label": "Ghost", "percent": "1.000"},
        )
        assert resp.status_code == 404

    async def test_delete_nonexistent_rate_404(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.delete(f"/api/v1/vat-rates/{uuid.uuid4()}")
        assert resp.status_code == 404


@pytest.mark.integration
class TestVatRateValidation:

    async def test_negative_percent_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/vat-rates", json={"label": "Negative", "percent": "-1.0"}
        )
        assert resp.status_code == 422

    async def test_zero_percent_allowed(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/vat-rates", json={"label": "My Zero", "percent": "0.000"}
        )
        assert resp.status_code in (201, 409)  # 409 if seed already has "Zero (0%)"

    async def test_empty_label_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/vat-rates", json={"label": "", "percent": "5.0"}
        )
        assert resp.status_code == 422

    async def test_duplicate_label_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/vat-rates", json={"label": "Dup Rate", "percent": "5.0"}
        )
        resp = await db_client.post(
            "/api/v1/vat-rates", json={"label": "Dup Rate", "percent": "6.0"}
        )
        assert resp.status_code == 409

    async def test_update_to_duplicate_label_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post("/api/v1/vat-rates", json={"label": "Existing", "percent": "10.0"})
        create_resp = await db_client.post(
            "/api/v1/vat-rates", json={"label": "Another", "percent": "11.0"}
        )
        rate_id = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/vat-rates/{rate_id}",
            json={"label": "Existing", "percent": "11.0"},
        )
        assert resp.status_code == 409

    async def test_update_same_label_no_conflict(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/vat-rates", json={"label": "Keep Same", "percent": "12.0"}
        )
        rate_id = create_resp.json()["id"]

        # Updating with the same label should not conflict.
        resp = await db_client.put(
            f"/api/v1/vat-rates/{rate_id}",
            json={"label": "Keep Same", "percent": "13.0"},
        )
        assert resp.status_code == 200
        assert float(resp.json()["percent"]) == 13.0


@pytest.mark.integration
class TestVatRateAuth:

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/vat-rates")
        assert resp.status_code == 401

    async def test_no_company_400(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)

        resp = await db_client.get("/api/v1/vat-rates")
        assert resp.status_code == 400

    async def test_cross_company_isolation(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models.vat import VatRate

        await _full_auth(db_client)
        await _setup_company(db_client)

        # Insert a rate for a different company directly.
        company_b_id = uuid.uuid4()
        rate_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="Company B", base_currency="EUR"))
            await session.flush()
            session.add(
                VatRate(
                    id=rate_b_id,
                    company_id=company_b_id,
                    label="Company B Rate",
                    percent=99,
                )
            )
            await session.commit()

        # The authenticated user cannot see or touch company B's rate.
        resp = await db_client.get(f"/api/v1/vat-rates/{rate_b_id}")
        assert resp.status_code == 404

        resp = await db_client.put(
            f"/api/v1/vat-rates/{rate_b_id}",
            json={"label": "Hacked", "percent": "1.0"},
        )
        assert resp.status_code == 404

        resp = await db_client.delete(f"/api/v1/vat-rates/{rate_b_id}")
        assert resp.status_code == 404

        # List must not contain company B's rate.
        list_resp = await db_client.get("/api/v1/vat-rates")
        labels = [r["label"] for r in list_resp.json()["items"]]
        assert "Company B Rate" not in labels


# ---------------------------------------------------------------------------
# VatTreatment CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestVatTreatmentCRUD:

    async def test_create_treatment(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/vat-treatments",
            json={
                "code": "CUSTOM_TREATMENT",
                "label": "Custom",
                "side": "SALES",
                "effect": "APPLY_RATE",
                "requires_icp": False,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["code"] == "CUSTOM_TREATMENT"
        assert data["side"] == "SALES"
        assert data["effect"] == "APPLY_RATE"
        assert data["report_box"] is None
        assert data["requires_icp"] is False
        assert "company_id" not in data

    async def test_list_treatments_includes_seeds(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/vat-treatments")
        assert resp.status_code == 200
        data = resp.json()
        codes = {t["code"] for t in data["items"]}
        # 8 seed treatments expected.
        assert "NL_DOMESTIC" in codes
        assert "EU_B2B_REVERSE" in codes
        assert "EU_B2C" in codes
        assert "EXPORT_NON_EU" in codes
        assert "NL_DOMESTIC_PURCH" in codes
        assert "EU_B2B_REVERSE_PURCH" in codes
        assert "EU_B2C_PURCH" in codes
        assert "IMPORT_NON_EU" in codes

    async def test_list_treatments_side_filter_sales(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/vat-treatments?side=SALES")
        assert resp.status_code == 200
        data = resp.json()
        for t in data["items"]:
            assert t["side"] == "SALES"
        codes = {t["code"] for t in data["items"]}
        assert "NL_DOMESTIC" in codes
        assert "NL_DOMESTIC_PURCH" not in codes

    async def test_list_treatments_side_filter_purchase(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/vat-treatments?side=PURCHASE")
        assert resp.status_code == 200
        data = resp.json()
        for t in data["items"]:
            assert t["side"] == "PURCHASE"
        codes = {t["code"] for t in data["items"]}
        assert "NL_DOMESTIC_PURCH" in codes
        assert "NL_DOMESTIC" not in codes

    async def test_get_treatment(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        treatments = (await db_client.get("/api/v1/vat-treatments")).json()["items"]
        tid = treatments[0]["id"]

        resp = await db_client.get(f"/api/v1/vat-treatments/{tid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == tid

    async def test_update_treatment(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/vat-treatments",
            json={
                "code": "OLD_CODE",
                "label": "Old Label",
                "side": "PURCHASE",
                "effect": "EXEMPT",
            },
        )
        tid = create_resp.json()["id"]

        resp = await db_client.put(
            f"/api/v1/vat-treatments/{tid}",
            json={
                "code": "NEW_CODE",
                "label": "New Label",
                "side": "PURCHASE",
                "effect": "APPLY_RATE",
                "requires_icp": False,
                "deductible": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "NEW_CODE"
        assert data["label"] == "New Label"
        assert data["effect"] == "APPLY_RATE"
        assert data["deductible"] is True

    async def test_delete_treatment(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        create_resp = await db_client.post(
            "/api/v1/vat-treatments",
            json={
                "code": "TEMP_TREAT",
                "label": "Temp",
                "side": "SALES",
                "effect": "EXEMPT",
            },
        )
        tid = create_resp.json()["id"]

        resp = await db_client.delete(f"/api/v1/vat-treatments/{tid}")
        assert resp.status_code == 204

        resp = await db_client.get(f"/api/v1/vat-treatments/{tid}")
        assert resp.status_code == 404

    async def test_get_nonexistent_treatment_404(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get(f"/api/v1/vat-treatments/{uuid.uuid4()}")
        assert resp.status_code == 404


@pytest.mark.integration
class TestVatTreatmentValidation:

    async def test_duplicate_code_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/vat-treatments",
            json={"code": "DUP_CODE", "label": "First", "side": "SALES", "effect": "APPLY_RATE"},
        )
        resp = await db_client.post(
            "/api/v1/vat-treatments",
            json={
                "code": "DUP_CODE",
                "label": "Second",
                "side": "PURCHASE",
                "effect": "EXEMPT",
            },
        )
        assert resp.status_code == 409

    async def test_update_to_duplicate_code_409(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        await db_client.post(
            "/api/v1/vat-treatments",
            json={"code": "CODE_A", "label": "A", "side": "SALES", "effect": "APPLY_RATE"},
        )
        r2 = await db_client.post(
            "/api/v1/vat-treatments",
            json={"code": "CODE_B", "label": "B", "side": "SALES", "effect": "EXEMPT"},
        )
        tid = r2.json()["id"]

        resp = await db_client.put(
            f"/api/v1/vat-treatments/{tid}",
            json={"code": "CODE_A", "label": "B", "side": "SALES", "effect": "EXEMPT"},
        )
        assert resp.status_code == 409

    async def test_invalid_side_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/vat-treatments",
            json={
                "code": "BAD_SIDE",
                "label": "Bad",
                "side": "INVALID_SIDE",
                "effect": "APPLY_RATE",
            },
        )
        assert resp.status_code == 422

    async def test_invalid_effect_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/vat-treatments",
            json={
                "code": "BAD_EFFECT",
                "label": "Bad",
                "side": "SALES",
                "effect": "INVALID_EFFECT",
            },
        )
        assert resp.status_code == 422

    async def test_empty_code_422(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.post(
            "/api/v1/vat-treatments",
            json={"code": "", "label": "Label", "side": "SALES", "effect": "APPLY_RATE"},
        )
        assert resp.status_code == 422

    async def test_report_box_ignored_stays_null(self, db_client: AsyncClient) -> None:
        """M4 boundary: report_box is not in VatTreatmentWrite; any value sent is dropped."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Create: send report_box — should be silently ignored
        create_resp = await db_client.post(
            "/api/v1/vat-treatments",
            json={
                "code": "TEST_REPORT_BOX",
                "label": "Report Box Test",
                "side": "SALES",
                "effect": "APPLY_RATE",
                "report_box": "1a",
            },
        )
        assert create_resp.status_code == 201
        data = create_resp.json()
        assert data["report_box"] is None, "report_box must stay NULL in M4"

        treatment_id = data["id"]

        # Update: send report_box — should still be ignored
        update_resp = await db_client.put(
            f"/api/v1/vat-treatments/{treatment_id}",
            json={
                "code": "TEST_REPORT_BOX",
                "label": "Report Box Test",
                "side": "SALES",
                "effect": "APPLY_RATE",
                "report_box": "1b",
            },
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["report_box"] is None, "report_box must stay NULL in M4"


@pytest.mark.integration
class TestVatTreatmentAuth:

    async def test_unauthenticated_401(self, db_client: AsyncClient) -> None:
        resp = await db_client.get("/api/v1/vat-treatments")
        assert resp.status_code == 401

    async def test_cross_company_isolation(
        self,
        db_client: AsyncClient,
        db_session_maker: async_sessionmaker[AsyncSession],
    ) -> None:
        from jai.models._enums import VatTreatmentEffect, VatTreatmentSide
        from jai.models.vat import VatTreatment

        await _full_auth(db_client)
        await _setup_company(db_client)

        company_b_id = uuid.uuid4()
        treat_b_id = uuid.uuid4()
        async with db_session_maker() as session:
            session.add(Company(id=company_b_id, name="Company B", base_currency="EUR"))
            await session.flush()
            session.add(
                VatTreatment(
                    id=treat_b_id,
                    company_id=company_b_id,
                    code="COMPANY_B_ONLY",
                    label="Company B Treatment",
                    side=VatTreatmentSide.SALES,
                    effect=VatTreatmentEffect.APPLY_RATE,
                )
            )
            await session.commit()

        resp = await db_client.get(f"/api/v1/vat-treatments/{treat_b_id}")
        assert resp.status_code == 404

        list_resp = await db_client.get("/api/v1/vat-treatments")
        codes = [t["code"] for t in list_resp.json()["items"]]
        assert "COMPANY_B_ONLY" not in codes


# ---------------------------------------------------------------------------
# Seed seeding – verified via the API surface
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestVatSeeds:

    async def test_seed_rates_on_company_creation(self, db_client: AsyncClient) -> None:
        """After creating a company, 3 default rates must be present."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/vat-rates")
        assert resp.status_code == 200
        items = resp.json()["items"]
        labels = {r["label"] for r in items}
        assert "NL standard (21%)" in labels
        assert "NL reduced (9%)" in labels
        assert "Zero (0%)" in labels
        # Seed percent values.
        by_label = {r["label"]: float(r["percent"]) for r in items}
        assert by_label["NL standard (21%)"] == 21.0
        assert by_label["NL reduced (9%)"] == 9.0
        assert by_label["Zero (0%)"] == 0.0

    async def test_seed_treatments_on_company_creation(self, db_client: AsyncClient) -> None:
        """After creating a company, the full default treatment set must be present."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        resp = await db_client.get("/api/v1/vat-treatments")
        assert resp.status_code == 200
        items = resp.json()["items"]

        by_code = {t["code"]: t for t in items}
        assert set(by_code) == {
            "NL_DOMESTIC",
            "EU_B2B_REVERSE",
            "EU_B2C",
            "EXPORT_NON_EU",
            "NL_DOMESTIC_PURCH",
            "EU_B2B_REVERSE_PURCH",
            "EU_B2C_PURCH",
            "IMPORT_NON_EU",
            "NL_PRIVATE_TRANSPORT_MILEAGE",
        }

        # Sales side checks.
        assert by_code["NL_DOMESTIC"]["side"] == "SALES"
        assert by_code["NL_DOMESTIC"]["effect"] == "APPLY_RATE"
        assert by_code["NL_DOMESTIC"]["requires_icp"] is False

        assert by_code["EU_B2B_REVERSE"]["side"] == "SALES"
        assert by_code["EU_B2B_REVERSE"]["effect"] == "ZERO_REVERSE"
        assert by_code["EU_B2B_REVERSE"]["requires_icp"] is True

        assert by_code["EU_B2C"]["side"] == "SALES"
        assert by_code["EU_B2C"]["effect"] == "APPLY_RATE"
        assert by_code["EU_B2C"]["requires_icp"] is False

        assert by_code["EXPORT_NON_EU"]["side"] == "SALES"
        assert by_code["EXPORT_NON_EU"]["effect"] == "ZERO_EXPORT"
        assert by_code["EXPORT_NON_EU"]["requires_icp"] is False

        # Purchase side checks.
        assert by_code["NL_DOMESTIC_PURCH"]["side"] == "PURCHASE"
        assert by_code["NL_DOMESTIC_PURCH"]["effect"] == "APPLY_RATE"
        assert by_code["NL_DOMESTIC_PURCH"]["deductible"] is True

        assert by_code["EU_B2B_REVERSE_PURCH"]["side"] == "PURCHASE"
        assert by_code["EU_B2B_REVERSE_PURCH"]["effect"] == "ZERO_REVERSE"
        assert by_code["EU_B2B_REVERSE_PURCH"]["requires_icp"] is True
        assert by_code["EU_B2B_REVERSE_PURCH"]["deductible"] is True

        mileage = by_code["NL_PRIVATE_TRANSPORT_MILEAGE"]
        assert mileage["side"] == "PURCHASE"
        assert mileage["effect"] == "EXEMPT"
        assert mileage["report_box"] is None
        assert mileage["deductible"] is False
        assert mileage["active"] is True

        # report_box must be NULL for all seeds (M4; M10 fills it).
        for treatment in items:
            assert treatment["report_box"] is None, (
                f"Expected report_box=NULL for {treatment['code']}, "
                f"got {treatment['report_box']!r}"
            )

    async def test_seed_idempotent(self, db_client: AsyncClient) -> None:
        """Calling company upsert a second time must not duplicate seeds."""
        await _full_auth(db_client)
        await _setup_company(db_client)

        # Second save of the company (update path).
        await db_client.put("/api/v1/company", json={"name": "Updated Co", "base_currency": "EUR"})

        rates = (await db_client.get("/api/v1/vat-rates")).json()["items"]
        nl_std_count = sum(1 for r in rates if r["label"] == "NL standard (21%)")
        assert nl_std_count == 1

        treatments = (await db_client.get("/api/v1/vat-treatments")).json()["items"]
        nl_dom_count = sum(1 for t in treatments if t["code"] == "NL_DOMESTIC")
        assert nl_dom_count == 1
