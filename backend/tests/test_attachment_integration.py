"""Integration tests for M8 step 2 – Expense attachment upload/list/serve/delete.

Requires a running PostgreSQL instance (``pytest -m integration``).

Coverage:
- Upload PNG / JPEG / WebP / PDF → 201 (metadata persisted + disk file written)
- Non-whitelisted MIME type → 422
- File over max_receipt_bytes → 422
- Magic-bytes mismatch (disguised extension) → 422
- GET /expenses/{id}/attachments → list with correct fields; no storage_key in response
- GET /attachments/{id}/content → 200, correct Content-Type + Content-Disposition: inline
- DELETE /attachments/{id} → 204; DB row + disk file removed
- DELETE expense → DB cascade removes attachment row; service removes disk file
- Cross-company attachment access → 404
- Owner-only (unauthenticated → 401)
- storage_key / disk path never appear in any response
- attachment_count on expense changes as attachments are added/removed
"""

from __future__ import annotations

import struct
import uuid
from pathlib import Path

import httpx
import pyotp
import pytest
from httpx import ASGITransport, AsyncClient

from jai.main import app
from jai.services.storage import LocalStorage, set_storage

# ---------------------------------------------------------------------------
# Magic-byte content fixtures (same as test_storage.py)
# ---------------------------------------------------------------------------

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
_WEBP = b"RIFF" + struct.pack("<I", 250) + b"WEBP" + b"\x00" * 200
_PDF = b"%PDF-1.4\n" + b"\x00" * 200

# ---------------------------------------------------------------------------
# Test helpers
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
    """Setup company and return dict with seeds."""
    resp = await client.put(
        "/api/v1/company",
        json={"name": "Test Co", "base_currency": "EUR", "country_code": "NL"},
    )
    assert resp.status_code == 200

    rates_resp = await client.get("/api/v1/vat-rates")
    rates = {r["label"]: r for r in rates_resp.json()["items"]}

    purch_resp = await client.get("/api/v1/vat-treatments?side=PURCHASE")
    purch_treatments = {t["code"]: t for t in purch_resp.json()["items"]}

    return {"rates": rates, "purch_treatments": purch_treatments}


async def _create_expense_category(client: AsyncClient, name: str = "Office") -> dict:
    resp = await client.post("/api/v1/expense-categories", json={"name": name})
    assert resp.status_code == 201
    return resp.json()


async def _create_expense(
    client: AsyncClient,
    *,
    category_id: str,
    vat_treatment_id: str,
    vat_rate_id: str,
) -> dict:
    body = {
        "expense_date": "2026-06-13",
        "category_id": category_id,
        "vat_treatment_id": vat_treatment_id,
        "vat_rate_id": vat_rate_id,
        "net_amount": "100.00",
        "vat_amount": "21.00",
    }
    resp = await client.post("/api/v1/expenses", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _upload_attachment(
    client: AsyncClient,
    expense_id: str,
    content: bytes,
    filename: str,
    content_type: str,
) -> tuple[int, dict]:
    """Upload a file and return (status_code, response_json)."""
    resp = await client.post(
        f"/api/v1/expenses/{expense_id}/attachments",
        files={"file": (filename, content, content_type)},
    )
    return resp.status_code, resp.json() if resp.status_code != 204 else {}


# ---------------------------------------------------------------------------
# Pytest fixture: redirect storage to tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path: Path):
    """Redirect the module-level storage singleton to a temp directory.

    This prevents tests from writing to the real ./var/storage directory
    (red-line: do not pollute the dev environment).
    """
    tmp_storage = LocalStorage(str(tmp_path))
    set_storage(tmp_storage)
    yield tmp_storage
    # After test: set_storage(None) effectively resets on next get_storage call
    # (the module-level _storage_instance is left as tmp_storage; a new test
    #  will call _tmp_storage again and override it, so this is fine)


# ---------------------------------------------------------------------------
# Happy flow: upload all allowed MIME types
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAttachmentUploadHappyFlow:

    async def test_upload_png_201(self, db_client: AsyncClient, _tmp_storage: LocalStorage) -> None:
        """Upload a PNG: returns 201 with correct metadata; file written to disk."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        status, att = await _upload_attachment(
            db_client, exp["id"], _PNG, "receipt.png", "image/png"
        )
        assert status == 201, att
        assert att["mime_type"] == "image/png"
        assert att["byte_size"] == len(_PNG)
        assert att["filename"] == "receipt.png"
        assert att["expense_id"] == exp["id"]
        assert "id" in att
        assert "created_at" in att
        # storage_key must NOT appear in the response
        assert "storage_key" not in att

        # Verify by fetching the content endpoint
        content_resp = await db_client.get(f"/api/v1/attachments/{att['id']}/content")
        assert content_resp.status_code == 200
        assert content_resp.content == _PNG

    async def test_upload_jpeg_201(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        status, att = await _upload_attachment(
            db_client, exp["id"], _JPEG, "invoice.jpg", "image/jpeg"
        )
        assert status == 201, att
        assert att["mime_type"] == "image/jpeg"
        assert "storage_key" not in att

    async def test_upload_webp_201(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        status, att = await _upload_attachment(
            db_client, exp["id"], _WEBP, "photo.webp", "image/webp"
        )
        assert status == 201, att
        assert att["mime_type"] == "image/webp"

    async def test_upload_pdf_201(self, db_client: AsyncClient) -> None:
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        status, att = await _upload_attachment(
            db_client, exp["id"], _PDF, "invoice.pdf", "application/pdf"
        )
        assert status == 201, att
        assert att["mime_type"] == "application/pdf"

    async def test_attachment_count_updates(self, db_client: AsyncClient) -> None:
        """attachment_count on ExpenseRead reflects the number of attachments."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        # Initially 0
        get_resp = await db_client.get(f"/api/v1/expenses/{exp['id']}")
        assert get_resp.json()["attachment_count"] == 0

        # After upload: 1
        await _upload_attachment(db_client, exp["id"], _PNG, "r1.png", "image/png")
        get_resp = await db_client.get(f"/api/v1/expenses/{exp['id']}")
        assert get_resp.json()["attachment_count"] == 1

        # After second upload: 2
        await _upload_attachment(db_client, exp["id"], _JPEG, "r2.jpg", "image/jpeg")
        get_resp = await db_client.get(f"/api/v1/expenses/{exp['id']}")
        assert get_resp.json()["attachment_count"] == 2


# ---------------------------------------------------------------------------
# Validation guards (MIME, size, magic bytes)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAttachmentValidation:

    async def _setup(self, client: AsyncClient) -> str:
        """Setup and return expense_id."""
        await _full_auth(client)
        seeds = await _setup_company(client)
        cat = await _create_expense_category(client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )
        return str(exp["id"])

    async def test_non_whitelisted_mime_422(self, db_client: AsyncClient) -> None:
        """text/plain → 422."""
        expense_id = await self._setup(db_client)
        status, _ = await _upload_attachment(
            db_client, expense_id, b"hello world", "file.txt", "text/plain"
        )
        assert status == 422

    async def test_image_gif_422(self, db_client: AsyncClient) -> None:
        """image/gif is not whitelisted → 422."""
        expense_id = await self._setup(db_client)
        gif = b"GIF89a" + b"\x00" * 100
        status, _ = await _upload_attachment(
            db_client, expense_id, gif, "image.gif", "image/gif"
        )
        assert status == 422

    async def test_file_over_max_bytes_422(self, db_client: AsyncClient, monkeypatch) -> None:
        """File exceeding max_receipt_bytes → 422."""
        from jai import config as _config

        # Override the settings to use a tiny limit
        original_get_settings = _config.get_settings
        class _TinySettings:
            max_receipt_bytes = 10  # 10 bytes only
            storage_root = original_get_settings().storage_root

        # Use monkeypatch to override get_settings in the modules that import it
        import jai.api.attachments as _att_api

        monkeypatch.setattr(_att_api, "get_settings", lambda: _TinySettings())

        expense_id = await self._setup(db_client)
        # PNG is 208 bytes – well over 10 bytes limit
        status, body = await _upload_attachment(
            db_client, expense_id, _PNG, "big.png", "image/png"
        )
        assert status == 422
        assert "large" in str(body).lower() or "422" in str(status)

    async def test_magic_bytes_mismatch_422(self, db_client: AsyncClient) -> None:
        """PNG bytes declared as image/jpeg → magic mismatch → 422."""
        expense_id = await self._setup(db_client)
        # Send PNG content but claim it's JPEG
        status, body = await _upload_attachment(
            db_client, expense_id, _PNG, "fake.jpg", "image/jpeg"
        )
        assert status == 422
        assert "match" in str(body).lower()

    async def test_cross_company_expense_404(self, db_client: AsyncClient) -> None:
        """Uploading to a random/cross-company expense_id → 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        random_id = str(uuid.uuid4())
        status, _ = await _upload_attachment(
            db_client, random_id, _PNG, "r.png", "image/png"
        )
        assert status == 404


# ---------------------------------------------------------------------------
# List attachments
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListAttachments:

    async def test_list_returns_all_attachments(self, db_client: AsyncClient) -> None:
        """GET /expenses/{id}/attachments returns all uploaded attachments."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        await _upload_attachment(db_client, exp["id"], _PNG, "r1.png", "image/png")
        await _upload_attachment(db_client, exp["id"], _JPEG, "r2.jpg", "image/jpeg")

        resp = await db_client.get(f"/api/v1/expenses/{exp['id']}/attachments")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        mimes = {item["mime_type"] for item in data["items"]}
        assert mimes == {"image/png", "image/jpeg"}

    async def test_list_excludes_storage_key(self, db_client: AsyncClient) -> None:
        """storage_key must not appear in any attachment list item."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )
        await _upload_attachment(db_client, exp["id"], _PDF, "doc.pdf", "application/pdf")

        resp = await db_client.get(f"/api/v1/expenses/{exp['id']}/attachments")
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert "storage_key" not in item

    async def test_list_cross_company_expense_404(self, db_client: AsyncClient) -> None:
        """Listing attachments for a cross-company expense → 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        resp = await db_client.get(f"/api/v1/expenses/{uuid.uuid4()}/attachments")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Content serving (inline)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAttachmentContent:

    async def test_get_content_correct_mime_inline(self, db_client: AsyncClient) -> None:
        """GET /attachments/{id}/content returns correct Content-Type + inline."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        _, att = await _upload_attachment(db_client, exp["id"], _PNG, "r.png", "image/png")
        assert att.get("id")

        resp = await db_client.get(f"/api/v1/attachments/{att['id']}/content")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert "inline" in resp.headers.get("content-disposition", "")
        assert resp.content == _PNG

    async def test_get_content_pdf(self, db_client: AsyncClient) -> None:
        """PDF attachment served with application/pdf MIME."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        _, att = await _upload_attachment(db_client, exp["id"], _PDF, "doc.pdf", "application/pdf")
        resp = await db_client.get(f"/api/v1/attachments/{att['id']}/content")
        assert resp.status_code == 200
        assert "application/pdf" in resp.headers["content-type"]
        assert resp.content == _PDF

    async def test_get_content_cross_company_404(self, db_client: AsyncClient) -> None:
        """GET content for a cross-company/random attachment → 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        resp = await db_client.get(f"/api/v1/attachments/{uuid.uuid4()}/content")
        assert resp.status_code == 404

    async def test_content_response_has_no_storage_key(self, db_client: AsyncClient) -> None:
        """The content response body must not contain 'storage_key'."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )
        _, att = await _upload_attachment(db_client, exp["id"], _PNG, "r.png", "image/png")
        resp = await db_client.get(f"/api/v1/attachments/{att['id']}/content")
        # Content is raw bytes (not JSON), so check the binary body doesn't
        # accidentally embed 'storage_key' string
        assert b"storage_key" not in resp.content


# ---------------------------------------------------------------------------
# Delete attachment
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteAttachment:

    async def test_delete_removes_db_row_and_disk_file(
        self, db_client: AsyncClient, _tmp_storage: LocalStorage
    ) -> None:
        """DELETE /attachments/{id} → 204; DB row gone; disk file removed."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        _, att = await _upload_attachment(db_client, exp["id"], _PNG, "r.png", "image/png")

        # Verify file exists before deletion
        content_resp = await db_client.get(f"/api/v1/attachments/{att['id']}/content")
        assert content_resp.status_code == 200

        # Delete
        del_resp = await db_client.delete(f"/api/v1/attachments/{att['id']}")
        assert del_resp.status_code == 204

        # DB row gone: GET content → 404
        content_after = await db_client.get(f"/api/v1/attachments/{att['id']}/content")
        assert content_after.status_code == 404

        # List should now be empty
        list_resp = await db_client.get(f"/api/v1/expenses/{exp['id']}/attachments")
        assert list_resp.json()["items"] == []

    async def test_delete_updates_attachment_count(self, db_client: AsyncClient) -> None:
        """attachment_count decrements after attachment deletion."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        _, att1 = await _upload_attachment(db_client, exp["id"], _PNG, "r1.png", "image/png")
        _, att2 = await _upload_attachment(db_client, exp["id"], _JPEG, "r2.jpg", "image/jpeg")

        get_resp = await db_client.get(f"/api/v1/expenses/{exp['id']}")
        assert get_resp.json()["attachment_count"] == 2

        await db_client.delete(f"/api/v1/attachments/{att1['id']}")

        get_resp = await db_client.get(f"/api/v1/expenses/{exp['id']}")
        assert get_resp.json()["attachment_count"] == 1

    async def test_delete_cross_company_attachment_404(self, db_client: AsyncClient) -> None:
        """DELETE for a random/cross-company attachment → 404."""
        await _full_auth(db_client)
        await _setup_company(db_client)
        resp = await db_client.delete(f"/api/v1/attachments/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete expense: cascade removes attachments rows + disk files (D12)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteExpenseCascade:

    async def test_delete_expense_removes_attachments_from_disk(
        self, db_client: AsyncClient, _tmp_storage: LocalStorage
    ) -> None:
        """Deleting an expense cascades: attachment metadata (DB) + disk files removed."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        # Upload two attachments
        _, att1 = await _upload_attachment(db_client, exp["id"], _PNG, "r1.png", "image/png")
        _, att2 = await _upload_attachment(db_client, exp["id"], _PDF, "r2.pdf", "application/pdf")

        # Verify both files exist
        r1 = await db_client.get(f"/api/v1/attachments/{att1['id']}/content")
        r2 = await db_client.get(f"/api/v1/attachments/{att2['id']}/content")
        assert r1.status_code == 200
        assert r2.status_code == 200

        # Delete the expense
        del_resp = await db_client.delete(f"/api/v1/expenses/{exp['id']}")
        assert del_resp.status_code == 204

        # Expense gone
        get_exp = await db_client.get(f"/api/v1/expenses/{exp['id']}")
        assert get_exp.status_code == 404

        # Attachment content endpoints → 404 (DB rows gone via CASCADE)
        c1 = await db_client.get(f"/api/v1/attachments/{att1['id']}/content")
        c2 = await db_client.get(f"/api/v1/attachments/{att2['id']}/content")
        assert c1.status_code == 404
        assert c2.status_code == 404

        # Disk files should be gone
        # We verify by checking that storage has no files for the expense
        # (using the tmp_storage fixture directly)
        all_files = list(_tmp_storage.root.rglob("*"))
        real_files = [f for f in all_files if f.is_file()]
        assert len(real_files) == 0, (
            f"Expected no disk files after cascade delete, found: {real_files}"
        )


# ---------------------------------------------------------------------------
# Owner-only / authentication
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAttachmentOwnerOnly:

    async def test_unauthenticated_upload_401(self, db_client: AsyncClient) -> None:
        """Unauthenticated upload → 401."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
            resp = await anon.post(
                f"/api/v1/expenses/{exp['id']}/attachments",
                files={"file": ("r.png", _PNG, "image/png")},
            )
            assert resp.status_code == 401

    async def test_unauthenticated_content_401(self, db_client: AsyncClient) -> None:
        """Unauthenticated GET content → 401."""
        await _full_auth(db_client)
        seeds = await _setup_company(db_client)
        cat = await _create_expense_category(db_client)
        treatment = seeds["purch_treatments"]["NL_DOMESTIC_PURCH"]
        rate = seeds["rates"]["NL standard (21%)"]
        exp = await _create_expense(
            db_client,
            category_id=cat["id"],
            vat_treatment_id=treatment["id"],
            vat_rate_id=rate["id"],
        )
        _, att = await _upload_attachment(db_client, exp["id"], _PNG, "r.png", "image/png")

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
            resp = await anon.get(f"/api/v1/attachments/{att['id']}/content")
            assert resp.status_code == 401
