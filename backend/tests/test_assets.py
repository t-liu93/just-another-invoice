"""Unit tests for ``jai.services.assets`` (M2 step 2).

All tests use mocks — no running PostgreSQL required.  Verifies:
- SVG sanitization (red-line 7) including external reference stripping
- Upload validation (mime whitelist, size limit)
- Company logo service logic (set / clear / get) with mocked DB
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jai.services.assets import (
    MAX_UPLOAD_BYTES,
    AssetValidationError,
    clear_company_logo,
    get_company_logo,
    sanitize_svg,
    set_company_logo,
    validate_upload,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_svg(extra: str = "") -> bytes:
    """Return a minimal valid SVG with optional injected content."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" fill="blue"/>
  {extra}
</svg>""".encode()


def _make_company_orm(logo_id: uuid.UUID | None = None) -> SimpleNamespace:
    """Build a mock Company ORM instance."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Test Co",
        logo_id=logo_id,
    )


# ---------------------------------------------------------------------------
# SVG sanitization
# ---------------------------------------------------------------------------


class TestSanitizeSvg:
    """Tests for ``sanitize_svg`` (red-line 7)."""

    def test_clean_svg_passes_through(self) -> None:
        """A clean SVG is preserved (modulo whitespace)."""
        raw = _minimal_svg()
        result = sanitize_svg(raw)
        assert b"<svg" in result
        assert b"<rect" in result

    def test_script_tag_stripped(self) -> None:
        """``<script>`` tags and their content are removed."""
        raw = _minimal_svg('<script>alert("xss")</script>')
        result = sanitize_svg(raw)
        assert b"<script" not in result
        assert b"alert" not in result

    def test_script_tag_with_src_stripped(self) -> None:
        """``<script src=...>`` is removed."""
        raw = _minimal_svg('<script src="https://evil.com/xss.js"></script>')
        result = sanitize_svg(raw)
        assert b"<script" not in result
        assert b"evil.com" not in result

    def test_onclick_attribute_stripped(self) -> None:
        """``onclick`` event handler attributes are removed."""
        raw = _minimal_svg('<rect onclick="alert(1)" width="10" height="10"/>')
        result = sanitize_svg(raw)
        assert b"onclick" not in result
        # The rect tag itself should survive.
        assert b"<rect" in result

    def test_onerror_attribute_stripped(self) -> None:
        """``onerror`` event handler attributes are removed."""
        raw = _minimal_svg('<image onerror="alert(1)" href="x"/>')
        result = sanitize_svg(raw)
        assert b"onerror" not in result

    def test_onmouseover_stripped(self) -> None:
        """``onmouseover`` event handler attributes are removed."""
        raw = _minimal_svg('<rect onmouseover="alert(1)" width="10"/>')
        result = sanitize_svg(raw)
        assert b"onmouseover" not in result

    def test_iframe_stripped(self) -> None:
        """``<iframe>`` elements are removed."""
        raw = _minimal_svg('<iframe src="https://evil.com"></iframe>')
        result = sanitize_svg(raw)
        assert b"<iframe" not in result

    def test_svg_with_safe_attributes_preserved(self) -> None:
        """Allowed SVG attributes like viewBox, fill are kept."""
        raw = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            b'<circle cx="50" cy="50" r="40" fill="red"/></svg>'
        )
        result = sanitize_svg(raw)
        assert b"viewBox" in result
        assert b'cx="50"' in result
        assert b'fill="red"' in result

    def test_javascript_href_stripped(self) -> None:
        """``javascript:`` in href is removed."""
        raw = _minimal_svg('<a href="javascript:alert(1)">click</a>')
        result = sanitize_svg(raw)
        assert b"javascript:" not in result

    def test_style_tag_content_stripped(self) -> None:
        """``<style>`` content is stripped (clean_content_tags)."""
        raw = _minimal_svg("<style>body{background:url('evil')}</style>")
        result = sanitize_svg(raw)
        assert b"<style" not in result
        assert b"background" not in result

    def test_comments_stripped(self) -> None:
        """HTML comments are stripped."""
        raw = _minimal_svg("<!-- secret comment -->")
        result = sanitize_svg(raw)
        assert b"<!--" not in result
        assert b"secret" not in result

    def test_multiple_dangerous_elements_stripped(self) -> None:
        """Multiple dangerous elements all stripped in one pass."""
        raw = _minimal_svg(
            '<script>alert(1)</script>'
            '<rect onmouseover="evil()" width="10"/>'
            "<!-- comment -->"
        )
        result = sanitize_svg(raw)
        assert b"<script" not in result
        assert b"onmouseover" not in result
        assert b"<!--" not in result

    # -- External reference stripping (red-line 7: SSRF prevention) ----------

    def test_use_external_href_https_stripped(self) -> None:
        """``<use href="https://...">`` external reference is removed."""
        raw = _minimal_svg('<use href="https://evil.example/sprite.svg#x"/>')
        result = sanitize_svg(raw)
        assert b"evil.example" not in result
        assert b'href="https' not in result

    def test_use_external_href_http_stripped(self) -> None:
        """``<use href="http://...">`` external reference is removed."""
        raw = _minimal_svg('<use href="http://evil.example/sprite.svg"/>')
        result = sanitize_svg(raw)
        assert b"evil.example" not in result
        assert b'href="http' not in result

    def test_use_xlink_href_external_stripped(self) -> None:
        """``<use xlink:href="https://...">`` is removed."""
        raw = _minimal_svg(
            '<use xlink:href="https://evil.example/sprite.svg#x"/>'
        )
        result = sanitize_svg(raw)
        assert b"evil.example" not in result
        assert b"xlink:href" not in result

    def test_use_local_fragment_preserved(self) -> None:
        """``<use href="#myShape">`` local fragment reference is kept."""
        raw = _minimal_svg("<use href=\"#myShape\"/>")
        result = sanitize_svg(raw)
        assert b"#myShape" in result

    def test_image_external_href_stripped(self) -> None:
        """``<image href="https://...">`` external reference is removed."""
        raw = _minimal_svg('<image href="https://evil.example/img.png"/>')
        result = sanitize_svg(raw)
        assert b"evil.example" not in result
        assert b'href="https' not in result

    def test_style_attribute_stripped(self) -> None:
        """``style`` attribute is removed (can embed url() references)."""
        raw = _minimal_svg('<rect style="fill:url(https://evil.example/a.svg#x)"/>')
        result = sanitize_svg(raw)
        assert b"style=" not in result
        assert b"evil.example" not in result

    def test_data_uri_href_stripped(self) -> None:
        """``<use href="data:...">`` is removed (not a local fragment)."""
        raw = _minimal_svg('<use href="data:image/svg+xml;base64,PHN2Zy8+"/>')
        result = sanitize_svg(raw)
        assert b"data:image" not in result

    def test_protocol_relative_href_stripped(self) -> None:
        """``<use href="//evil.example/...">`` is removed."""
        raw = _minimal_svg('<use href="//evil.example/sprite.svg"/>')
        result = sanitize_svg(raw)
        assert b"evil.example" not in result

    # -- Paint attribute external url() stripping (red-line 7) ---------------

    def test_fill_external_url_stripped(self) -> None:
        """``fill="url(https://...)"`` external reference is removed."""
        raw = _minimal_svg('<rect fill="url(https://evil.example/p.svg#p)" width="10"/>')
        result = sanitize_svg(raw)
        assert b"evil.example" not in result
        assert b'fill="url(' not in result

    def test_stroke_external_url_stripped(self) -> None:
        """``stroke="url(https://...)"`` external reference is removed."""
        raw = _minimal_svg('<rect stroke="url(https://evil.example/s.svg)" width="10"/>')
        result = sanitize_svg(raw)
        assert b"evil.example" not in result
        assert b'stroke="url(' not in result

    def test_fill_local_url_fragment_preserved(self) -> None:
        """``fill="url(#gradient)"`` local fragment is preserved."""
        raw = _minimal_svg('<rect fill="url(#myGrad)" width="10"/>')
        result = sanitize_svg(raw)
        assert b"url(#myGrad)" in result

    def test_stroke_local_url_fragment_preserved(self) -> None:
        """``stroke="url(#pattern)"`` local fragment is preserved."""
        raw = _minimal_svg('<rect stroke="url(#myPat)" width="10"/>')
        result = sanitize_svg(raw)
        assert b"url(#myPat)" in result

    def test_fill_color_literal_preserved(self) -> None:
        """Color literal values (``red``, ``#ff0000``) are unaffected."""
        raw = _minimal_svg('<rect fill="red" width="10"/>')
        result = sanitize_svg(raw)
        assert b'fill="red"' in result

    def test_fill_hex_color_preserved(self) -> None:
        """Hex color values are unaffected."""
        raw = _minimal_svg('<rect fill="#ff0000" stroke="#00ff00" width="10"/>')
        result = sanitize_svg(raw)
        assert b'fill="#ff0000"' in result
        assert b'stroke="#00ff00"' in result

    def test_fill_data_uri_stripped(self) -> None:
        """``fill="url(data:...)"`` is removed."""
        raw = _minimal_svg('<rect fill="url(data:image/svg+xml;base64,PHN2Zy8+)" width="10"/>')
        result = sanitize_svg(raw)
        assert b"data:image" not in result


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------


class TestValidateUpload:
    """Tests for ``validate_upload``."""

    def test_valid_png_passes(self) -> None:
        """A valid PNG within size limits passes."""
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = validate_upload(content, "image/png")
        assert result == content

    def test_valid_jpeg_passes(self) -> None:
        """A valid JPEG passes."""
        content = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        result = validate_upload(content, "image/jpeg")
        assert result == content

    def test_valid_webp_passes(self) -> None:
        """A valid WebP passes."""
        content = b"RIFF" + b"\x00" * 100
        result = validate_upload(content, "image/webp")
        assert result == content

    def test_svg_gets_sanitized(self) -> None:
        """SVG content is sanitized through validate_upload."""
        content = _minimal_svg('<script>alert("xss")</script>')
        result = validate_upload(content, "image/svg+xml")
        assert b"<script" not in result
        assert b"<svg" in result

    def test_unsupported_mime_rejected(self) -> None:
        """Unsupported MIME type raises AssetValidationError."""
        with pytest.raises(AssetValidationError, match="Unsupported file type"):
            validate_upload(b"data", "application/pdf")

    def test_gif_rejected(self) -> None:
        """GIF is not in the whitelist."""
        with pytest.raises(AssetValidationError, match="Unsupported file type"):
            validate_upload(b"GIF89a", "image/gif")

    def test_oversized_file_rejected(self) -> None:
        """File exceeding MAX_UPLOAD_BYTES raises AssetValidationError."""
        content = b"\x00" * (MAX_UPLOAD_BYTES + 1)
        with pytest.raises(AssetValidationError, match="File too large"):
            validate_upload(content, "image/png")

    def test_max_size_boundary_passes(self) -> None:
        """File at exactly MAX_UPLOAD_BYTES passes."""
        content = b"\x00" * MAX_UPLOAD_BYTES
        result = validate_upload(content, "image/png")
        assert len(result) == MAX_UPLOAD_BYTES

    def test_octet_stream_rejected(self) -> None:
        """application/octet-stream is not in whitelist."""
        with pytest.raises(AssetValidationError, match="Unsupported file type"):
            validate_upload(b"data", "application/octet-stream")


# ---------------------------------------------------------------------------
# Company logo service (mocked DB)
# ---------------------------------------------------------------------------


class TestSetCompanyLogo:
    """Tests for ``set_company_logo``."""

    async def test_upload_creates_asset(self) -> None:
        """Uploading a logo creates a BinaryAsset and links company.logo_id."""
        company = _make_company_orm()
        session = AsyncMock()
        added_assets: list[object] = []

        def _track_add(obj: object) -> None:
            added_assets.append(obj)

        session.add = MagicMock(side_effect=_track_add)

        async def _fake_flush() -> None:
            # Simulate what the real flush does: assign id to new assets.
            for obj in added_assets:
                if hasattr(obj, "id") and obj.id is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=_fake_flush)

        with patch("jai.services.assets.get_company", return_value=company):
            with patch("jai.services.assets.validate_upload", side_effect=lambda c, *a, **kw: c):
                result = await set_company_logo(
                    session, b"fake-png-data", "image/png", "logo.png"
                )

        assert result["mime_type"] == "image/png"
        assert result["logo_url"].startswith("/api/v1/company/logo?v=")
        assert company.logo_id is not None
        # session.add was called to create the asset.
        assert len(added_assets) == 1
        asset = added_assets[0]
        assert asset.content == b"fake-png-data"
        assert asset.byte_size == len(b"fake-png-data")

    async def test_upload_replaces_existing_logo(self) -> None:
        """Uploading a new logo deletes the old asset first."""
        old_logo_id = uuid.uuid4()
        company = _make_company_orm(logo_id=old_logo_id)
        session = AsyncMock()
        old_asset = MagicMock()
        session.get = AsyncMock(return_value=old_asset)

        # Fix: use MagicMock for session.add to avoid AsyncMock warning.
        added_assets: list[object] = []

        def _track_add(obj: object) -> None:
            added_assets.append(obj)

        session.add = MagicMock(side_effect=_track_add)

        async def _fake_flush() -> None:
            for obj in added_assets:
                if hasattr(obj, "id") and obj.id is None:
                    obj.id = uuid.uuid4()

        session.flush = AsyncMock(side_effect=_fake_flush)

        with patch("jai.services.assets.get_company", return_value=company):
            with patch("jai.services.assets.validate_upload", side_effect=lambda c, *a, **kw: c):
                await set_company_logo(
                    session, b"new-logo", "image/png", "new.png"
                )

        # Old asset was deleted.
        assert any(
            call[0][0] is old_asset
            for call in session.delete.call_args_list
        )
        # New asset was added.
        assert len(added_assets) == 1
        assert company.logo_id != old_logo_id

    async def test_upload_fails_without_company(self) -> None:
        """Uploading logo before company is created raises error."""
        session = AsyncMock()

        with patch("jai.services.assets.get_company", return_value=None):
            with patch("jai.services.assets.validate_upload", side_effect=lambda c, *a, **kw: c):
                with pytest.raises(AssetValidationError, match="Company profile must be created"):
                    await set_company_logo(
                        session, b"data", "image/png"
                    )


class TestClearCompanyLogo:
    """Tests for ``clear_company_logo``."""

    async def test_clear_deletes_asset(self) -> None:
        """Clearing logo deletes the binary_asset and unsets logo_id."""
        logo_id = uuid.uuid4()
        company = _make_company_orm(logo_id=logo_id)
        session = AsyncMock()
        asset = MagicMock()
        session.get = AsyncMock(return_value=asset)

        with patch("jai.services.assets.get_company", return_value=company):
            result = await clear_company_logo(session)

        assert result is True
        assert company.logo_id is None
        session.delete.assert_called_once_with(asset)

    async def test_clear_no_logo_returns_false(self) -> None:
        """Clearing when no logo exists returns False."""
        company = _make_company_orm(logo_id=None)
        session = AsyncMock()

        with patch("jai.services.assets.get_company", return_value=company):
            result = await clear_company_logo(session)

        assert result is False

    async def test_clear_no_company_returns_false(self) -> None:
        """Clearing when no company exists returns False."""
        session = AsyncMock()

        with patch("jai.services.assets.get_company", return_value=None):
            result = await clear_company_logo(session)

        assert result is False


class TestGetCompanyLogo:
    """Tests for ``get_company_logo``."""

    async def test_returns_content_and_mime(self) -> None:
        """Returns (bytes, mime_type) when logo exists."""
        logo_id = uuid.uuid4()
        company = _make_company_orm(logo_id=logo_id)
        session = AsyncMock()
        asset = SimpleNamespace(content=b"img-data", mime_type="image/png")
        session.get = AsyncMock(return_value=asset)

        with patch("jai.services.assets.get_company", return_value=company):
            result = await get_company_logo(session)

        assert result is not None
        assert result == (b"img-data", "image/png")

    async def test_returns_none_when_no_logo(self) -> None:
        """Returns None when company has no logo."""
        company = _make_company_orm(logo_id=None)
        session = AsyncMock()

        with patch("jai.services.assets.get_company", return_value=company):
            result = await get_company_logo(session)

        assert result is None

    async def test_returns_none_when_no_company(self) -> None:
        """Returns None when company doesn't exist."""
        session = AsyncMock()

        with patch("jai.services.assets.get_company", return_value=None):
            result = await get_company_logo(session)

        assert result is None
