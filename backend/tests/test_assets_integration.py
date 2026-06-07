"""Integration tests for company logo upload/serve/delete (M2 step 2).

Requires a running PostgreSQL instance (``pytest -m integration``).

Covers:
- Upload PNG logo → 200, GET returns correct binary
- Upload SVG logo → sanitized, GET returns correct content
- Replace logo → old deleted, new served
- Delete logo → 204, GET returns 404
- Unsupported mime → 400
- Oversized file → 400
- SVG with <script> → sanitized on upload, clean served
- No logo → 404
- Unauthenticated → 401
- Full auth flow: register → MFA → company → logo CRUD
"""

from __future__ import annotations

import pyotp
import pytest
from httpx import AsyncClient  # noqa: I001

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _full_auth(
    client: AsyncClient,
    email: str = "owner@example.com",
    password: str = "testpassword1",
) -> None:
    """Register → login → MFA setup → MFA verify."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200

    resp = await client.post("/api/v1/auth/mfa/setup")
    assert resp.status_code == 200
    secret = resp.json()["secret"]

    code = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/api/v1/auth/mfa/verify",
        json={"code": code},
    )
    assert resp.status_code == 204


async def _create_company(client: AsyncClient) -> None:
    """Create the singleton company (required before logo upload)."""
    resp = await client.put(
        "/api/v1/company",
        json={"name": "Logo Test Co", "base_currency": "EUR"},
    )
    assert resp.status_code == 200


def _minimal_svg(extra: str = "") -> bytes:
    """Return a minimal SVG with optional injected content."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f"<rect width='100' height='100' fill='blue'/>"
        f"{extra}"
        f"</svg>"
    ).encode()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLogoAPI:
    """Integration tests for PUT/DELETE/GET /api/v1/company/logo."""

    async def test_upload_png_logo(self, db_client: AsyncClient) -> None:
        """Upload a PNG logo → 200, can be fetched back."""
        await _full_auth(db_client)
        await _create_company(db_client)

        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("logo.png", png_data, "image/png")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mime_type"] == "image/png"
        assert data["byte_size"] == len(png_data)
        assert data["logo_url"].startswith("/api/v1/company/logo?v=")

        # GET logo back
        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 200
        assert resp.content == png_data
        assert resp.headers["content-type"] == "image/png"
        assert "etag" in resp.headers
        assert "cache-control" in resp.headers

    async def test_upload_svg_logo_sanitized(
        self, db_client: AsyncClient
    ) -> None:
        """Upload an SVG with <script> → sanitized, clean served."""
        await _full_auth(db_client)
        await _create_company(db_client)

        svg_data = _minimal_svg('<script>alert("xss")</script>')
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("logo.svg", svg_data, "image/svg+xml")},
        )
        assert resp.status_code == 200
        assert resp.json()["mime_type"] == "image/svg+xml"

        # Fetch and verify script was stripped
        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 200
        content = resp.content
        assert b"<script" not in content
        assert b"alert" not in content
        assert b"<svg" in content
        assert b"<rect" in content

    async def test_upload_svg_with_event_handler_stripped(
        self, db_client: AsyncClient
    ) -> None:
        """SVG with onclick → event handler stripped on upload."""
        await _full_auth(db_client)
        await _create_company(db_client)

        svg_data = _minimal_svg('<rect onclick="evil()" width="10"/>')
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("evil.svg", svg_data, "image/svg+xml")},
        )
        assert resp.status_code == 200

        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 200
        assert b"onclick" not in resp.content

    async def test_replace_logo_deletes_old(
        self, db_client: AsyncClient
    ) -> None:
        """Replace logo → old one is deleted, new one is served."""
        await _full_auth(db_client)
        await _create_company(db_client)

        # Upload first logo
        old_data = b"\x89PNG\r\n\x1a\nOLD"
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("old.png", old_data, "image/png")},
        )
        assert resp.status_code == 200

        # Replace with new
        new_data = b"\x89PNG\r\n\x1a\nNEW"
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("new.png", new_data, "image/png")},
        )
        assert resp.status_code == 200

        # GET returns new data
        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 200
        assert resp.content == new_data

        # Company profile shows has_logo=True
        resp = await db_client.get("/api/v1/company")
        assert resp.json()["has_logo"] is True

    async def test_delete_logo(self, db_client: AsyncClient) -> None:
        """Delete logo → 204, subsequent GET returns 404."""
        await _full_auth(db_client)
        await _create_company(db_client)

        # Upload
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("logo.png", b"\x89PNG", "image/png")},
        )
        assert resp.status_code == 200

        # Delete
        resp = await db_client.delete("/api/v1/company/logo")
        assert resp.status_code == 204

        # GET returns 404
        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 404

        # Company profile shows has_logo=False
        resp = await db_client.get("/api/v1/company")
        assert resp.json()["has_logo"] is False
        assert resp.json()["logo_url"] is None

    async def test_delete_no_logo_returns_404(
        self, db_client: AsyncClient
    ) -> None:
        """Delete when no logo → 404."""
        await _full_auth(db_client)
        await _create_company(db_client)

        resp = await db_client.delete("/api/v1/company/logo")
        assert resp.status_code == 404

    async def test_get_no_logo_returns_404(
        self, db_client: AsyncClient
    ) -> None:
        """GET logo when none uploaded → 404."""
        await _full_auth(db_client)
        await _create_company(db_client)

        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 404

    async def test_unsupported_mime_returns_400(
        self, db_client: AsyncClient
    ) -> None:
        """Uploading a GIF (not in whitelist) → 400."""
        await _full_auth(db_client)
        await _create_company(db_client)

        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("logo.gif", b"GIF89a", "image/gif")},
        )
        assert resp.status_code == 400
        assert "Unsupported file type" in resp.json()["detail"]

    async def test_oversized_file_returns_400(
        self, db_client: AsyncClient
    ) -> None:
        """File > 512 KB → 400."""
        await _full_auth(db_client)
        await _create_company(db_client)

        big_data = b"\x00" * (512 * 1024 + 1)
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("big.png", big_data, "image/png")},
        )
        assert resp.status_code == 400
        assert "File too large" in resp.json()["detail"]

    async def test_upload_before_company_returns_400(
        self, db_client: AsyncClient
    ) -> None:
        """Upload logo before creating company → 400."""
        await _full_auth(db_client)
        # Don't create company.

        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("logo.png", b"\x89PNG", "image/png")},
        )
        assert resp.status_code == 400

    async def test_unauthenticated_returns_401(
        self, db_client: AsyncClient
    ) -> None:
        """Unauthenticated requests → 401."""
        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 401

        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("logo.png", b"data", "image/png")},
        )
        assert resp.status_code == 401

        resp = await db_client.delete("/api/v1/company/logo")
        assert resp.status_code == 401

    async def test_upload_webp_logo(self, db_client: AsyncClient) -> None:
        """WebP upload works."""
        await _full_auth(db_client)
        await _create_company(db_client)

        webp_data = b"RIFF\x00\x00\x00\x00WEBP"
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("logo.webp", webp_data, "image/webp")},
        )
        assert resp.status_code == 200
        assert resp.json()["mime_type"] == "image/webp"

    async def test_upload_jpeg_logo(self, db_client: AsyncClient) -> None:
        """JPEG upload works."""
        await _full_auth(db_client)
        await _create_company(db_client)

        jpeg_data = b"\xff\xd8\xff\xe0JFIF"
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("logo.jpg", jpeg_data, "image/jpeg")},
        )
        assert resp.status_code == 200
        assert resp.json()["mime_type"] == "image/jpeg"

    async def test_etag_cache_header_set(
        self, db_client: AsyncClient
    ) -> None:
        """GET logo includes ETag and Cache-Control headers."""
        await _full_auth(db_client)
        await _create_company(db_client)

        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("logo.png", b"\x89PNG", "image/png")},
        )
        assert resp.status_code == 200

        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 200
        assert resp.headers["etag"].startswith('"')
        assert "private" in resp.headers["cache-control"]
        assert "max-age=3600" in resp.headers["cache-control"]

    async def test_upload_svg_external_href_stripped(
        self, db_client: AsyncClient
    ) -> None:
        """SVG with external <use href=https://...> is stripped on upload."""
        await _full_auth(db_client)
        await _create_company(db_client)

        svg_data = _minimal_svg(
            '<use href="https://evil.example/sprite.svg#x"/>'
        )
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("ext.svg", svg_data, "image/svg+xml")},
        )
        assert resp.status_code == 200

        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 200
        assert b"evil.example" not in resp.content

    async def test_upload_svg_local_fragment_preserved(
        self, db_client: AsyncClient
    ) -> None:
        """SVG with local <use href=#id> is preserved."""
        await _full_auth(db_client)
        await _create_company(db_client)

        svg_data = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            b"<defs><rect id='r' width='10' height='10'/></defs>"
            b"<use href='#r'/>"
            b"</svg>"
        )
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("local.svg", svg_data, "image/svg+xml")},
        )
        assert resp.status_code == 200

        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 200
        assert b"#r" in resp.content

    async def test_replace_logo_cache_busts_url(
        self, db_client: AsyncClient
    ) -> None:
        """Replace logo → both upload response and CompanyRead logo_url change."""
        await _full_auth(db_client)
        await _create_company(db_client)

        # Upload first
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("a.png", b"\x89PNG_A", "image/png")},
        )
        assert resp.status_code == 200
        url_v1 = resp.json()["logo_url"]
        assert "?v=" in url_v1

        # Check CompanyRead logo_url before replacement
        resp = await db_client.get("/api/v1/company")
        company_url_v1 = resp.json()["logo_url"]
        assert company_url_v1 is not None
        assert "?v=" in company_url_v1

        # Replace
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("b.png", b"\x89PNG_B", "image/png")},
        )
        assert resp.status_code == 200
        url_v2 = resp.json()["logo_url"]
        assert url_v2 != url_v1

        # Verify CompanyRead logo_url also changed after replacement.
        resp = await db_client.get("/api/v1/company")
        company_url_v2 = resp.json()["logo_url"]
        assert company_url_v2 is not None
        assert company_url_v2 != company_url_v1

    async def test_upload_svg_fill_external_url_stripped(
        self, db_client: AsyncClient
    ) -> None:
        """SVG with fill="url(https://...)" is stripped on upload."""
        await _full_auth(db_client)
        await _create_company(db_client)

        svg_data = _minimal_svg(
            '<rect fill="url(https://evil.example/paint.svg#p)" width="10" height="10"/>'
        )
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("paint.svg", svg_data, "image/svg+xml")},
        )
        assert resp.status_code == 200

        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 200
        assert b"evil.example" not in resp.content
        # The rect element itself should still exist.
        assert b"<rect" in resp.content

    async def test_upload_svg_fill_local_url_preserved(
        self, db_client: AsyncClient
    ) -> None:
        """SVG with fill="url(#localGrad)" is preserved."""
        await _full_auth(db_client)
        await _create_company(db_client)

        svg_data = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            b"<defs><linearGradient id='g1'>"
            b"<stop offset='0' stop-color='red'/>"
            b"</linearGradient></defs>"
            b"<rect fill='url(#g1)' width='10' height='10'/>"
            b"</svg>"
        )
        resp = await db_client.put(
            "/api/v1/company/logo",
            files={"file": ("local-paint.svg", svg_data, "image/svg+xml")},
        )
        assert resp.status_code == 200

        resp = await db_client.get("/api/v1/company/logo")
        assert resp.status_code == 200
        assert b"url(#g1)" in resp.content
