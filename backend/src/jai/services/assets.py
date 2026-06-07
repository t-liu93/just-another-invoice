"""Asset upload / serve / delete service (M2 step 2).

Public API
----------
- ``sanitize_svg`` – strip dangerous elements/attributes from SVG content.
- ``validate_upload`` – check mime type, size, and sanitize SVGs.
- ``set_company_logo`` – upload a new logo (upsert: delete old, insert new).
- ``clear_company_logo`` – remove the company logo.
- ``get_company_logo`` – retrieve logo bytes + metadata.

Design notes
------------
- Mime whitelist: PNG, JPEG, WebP, SVG (red-line 7: SVG is sanitized).
- Size limit: 512 KB.
- SVG sanitization uses ``nh3`` (Ammonia bindings) to strip ``<script>``,
  event handlers (``on*``), and dangerous content.  Post-processing passes
  strip external URL references:
  (1) ``href``/``xlink:href`` with non-local schemes;
  (2) ``url(https://...)`` etc. in paint attributes (``fill``, ``stroke``);
  (3) ``style`` attribute excluded from allow-list (CSS ``url()`` risk).
  This fully satisfies red-line 7 (XSS/SSRF prevention).
- ``company.logo_id`` is managed here; the FK ``ON DELETE SET NULL`` ensures
  the company row stays valid when a binary_asset is deleted.
"""

from __future__ import annotations

import re
from typing import Any

import nh3
from sqlalchemy.ext.asyncio import AsyncSession

from jai.models.binary_asset import BinaryAsset
from jai.services.company import get_company

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum upload size in bytes (512 KB).
MAX_UPLOAD_BYTES: int = 512 * 1024

#: Allowed MIME types for logo uploads.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
)

#: SVG-specific tags that ``nh3`` should allow.
_SVG_CLEAN_TAGS: frozenset[str] = frozenset(
    {
        "a", "abbr", "acronym", "address", "area", "article", "aside",
        "audio", "b", "bdi", "bdo", "blockquote", "br", "button",
        "canvas", "caption", "center", "cite", "code", "col", "colgroup",
        "data", "dd", "del", "details", "dfn", "dialog", "div", "dl", "dt",
        "em", "fieldset", "figcaption", "figure", "footer", "form",
        "g", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup",
        "hr", "i", "iframe", "img", "input", "ins", "kbd", "label",
        "legend", "li", "main", "map", "mark", "menu", "meter", "nav",
        "ol", "optgroup", "option", "output", "p", "path", "picture",
        "polygon", "polyline", "pre", "progress", "q", "rect", "rp", "rt",
        "ruby", "s", "samp", "section", "select", "small", "source", "span",
        "strike", "strong", "sub", "summary", "sup", "svg", "circle",
        "ellipse", "line", "text", "tspan", "table", "tbody", "td",
        "template", "textarea", "tfoot", "th", "thead", "time", "tr",
        "track", "tt", "u", "ul", "use", "var", "video", "wbr",
    }
)

#: SVG-specific attributes that ``nh3`` should allow, keyed by tag name.
#: ``*`` means "allowed on any tag".
#:
#: **Note** (red-line 7): ``style`` is intentionally *not* in the allow-list
#: because CSS ``url()`` can reference external resources (SSRF risk).
#: ``href``/``xlink:href`` are allowed on ``use``/``image`` but a
#: post-processing step strips non-local (``#id``) references.
_SVG_CLEAN_ATTRS: dict[str, frozenset[str]] = {
    "*": frozenset(
        {
            "class", "id", "aria-label", "aria-hidden", "role",
            "tabindex", "focusable", "opacity",
        }
    ),
    "svg": frozenset(
        {
            "viewBox", "xmlns", "version", "preserveAspectRatio",
            "width", "height", "x", "y",
        }
    ),
    "g": frozenset({"transform"}),
    "path": frozenset({"d", "transform", "fill", "stroke", "stroke-width"}),
    "circle": frozenset({"cx", "cy", "r", "fill", "stroke", "stroke-width", "transform"}),
    "ellipse": frozenset({"cx", "cy", "rx", "ry", "fill", "stroke", "stroke-width", "transform"}),
    "line": frozenset({"x1", "y1", "x2", "y2", "stroke", "stroke-width", "transform"}),
    "rect": frozenset(
        {"x", "y", "width", "height", "rx", "ry", "fill", "stroke", "stroke-width", "transform"}
    ),
    "polygon": frozenset({"points", "fill", "stroke", "stroke-width", "transform"}),
    "polyline": frozenset({"points", "fill", "stroke", "stroke-width", "transform"}),
    "text": frozenset(
        {
            "x", "y", "dx", "dy", "font-size", "font-family",
            "text-anchor", "dominant-baseline", "fill", "transform",
        }
    ),
    "tspan": frozenset({"x", "y", "dx", "dy", "font-size", "font-family", "fill"}),
    "use": frozenset({"href", "xlink:href", "x", "y", "width", "height", "transform"}),
    "image": frozenset({"href", "xlink:href", "x", "y", "width", "height", "transform"}),
    "linearGradient": frozenset({"id", "x1", "y1", "x2", "y2"}),
    "radialGradient": frozenset({"id", "cx", "cy", "r", "fx", "fy"}),
    "stop": frozenset({"offset", "stop-color", "stop-opacity"}),
    "defs": frozenset(),
    "clipPath": frozenset({"id"}),
}

#: Regex matching external URL schemes that must be stripped from SVG hrefs.
#: Only local fragment references (``#id``) are safe.
_EXTERNAL_HREF_RE: re.Pattern[str] = re.compile(
    r"""(?:href|xlink:href)\s*=\s*["'](?!\#)[^"']*["']""",
    re.IGNORECASE,
)

#: Regex matching external ``url()`` references in paint attributes.
#: SVG paint attributes (``fill``, ``stroke``) can reference paint servers
#: via ``url(...)``.  Only local fragment references (``url(#id)``) are safe;
#: anything else (``url(https://...)``, ``url(data:...)``, etc.) is stripped
#: to prevent SSRF.  Color literal values (``red``, ``#ff0000``, etc.) are
#: unaffected because they do not contain ``url(``.
_EXTERNAL_PAINT_URL_RE: re.Pattern[str] = re.compile(
    r"""(?:fill|stroke)\s*=\s*["']url\((?!\#)[^)]*\)["']""",
    re.IGNORECASE,
)


class AssetValidationError(ValueError):
    """Raised when an uploaded asset fails validation."""


# ---------------------------------------------------------------------------
# SVG sanitization (unit-testable without DB)
# ---------------------------------------------------------------------------


def _strip_external_hrefs(svg_text: str) -> str:
    """Remove external URL references from href/xlink:href attributes.

    Only local fragment references (``#id``) are kept; anything else
    (``http://``, ``https://``, ``data:``, protocol-relative ``//``, etc.)
    is stripped entirely.  This prevents SSRF via SVG references.
    """
    return _EXTERNAL_HREF_RE.sub("", svg_text)


def _strip_external_paint_urls(svg_text: str) -> str:
    """Remove external ``url()`` references from paint attributes.

    SVG paint attributes like ``fill`` and ``stroke`` can reference paint
    servers (gradients, patterns) via ``url(...)``.  Only local fragment
    references (``url(#id)``) are safe; any non-local URL is stripped to
    prevent SSRF.
    """
    return _EXTERNAL_PAINT_URL_RE.sub("", svg_text)


def sanitize_svg(raw: bytes) -> bytes:
    """Sanitize SVG content by stripping dangerous elements/attributes.

    Two-pass approach:
    1. ``nh3`` (Ammonia) strips ``<script>``, ``<style>``, event handlers
       (``on*``), unapproved tags/attributes, and comments.
    2. Post-processing strips ``href``/``xlink:href`` attributes that
       reference non-local URLs (preventing SSRF via external references).
    3. Post-processing strips external ``url()`` references from paint
       attributes (``fill``, ``stroke``), keeping only ``url(#id)``.

    The ``style`` attribute is intentionally excluded from the allow-list
    because CSS ``url()`` can embed external references.

    Parameters
    ----------
    raw:
        Raw SVG bytes (expected UTF-8).

    Returns
    -------
    Sanitized SVG bytes.
    """
    text_content = raw.decode("utf-8", errors="replace")

    # Pass 1: nh3 structural sanitization.
    cleaned = nh3.clean(
        text_content,
        tags=_SVG_CLEAN_TAGS,
        attributes=_SVG_CLEAN_ATTRS,
        clean_content_tags={"script", "style"},
        strip_comments=True,
        link_rel=None,
    )

    # Pass 2: strip external URL references from href/xlink:href.
    cleaned = _strip_external_hrefs(cleaned)

    # Pass 3: strip external url() from paint attributes (fill, stroke).
    cleaned = _strip_external_paint_urls(cleaned)

    return cleaned.encode("utf-8")


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------


def validate_upload(
    content: bytes,
    mime_type: str,
    filename: str | None = None,
) -> bytes:
    """Validate an uploaded file and return (possibly sanitized) content.

    Checks:
    1. MIME type is in the whitelist.
    2. File size is within the limit.
    3. SVG content is sanitized (red-line 7).

    Parameters
    ----------
    content:
        Raw file bytes.
    mime_type:
        MIME type as reported by the client / detected.
    filename:
        Original filename (metadata only).

    Returns
    -------
    The file bytes (sanitized for SVG, unchanged otherwise).

    Raises
    ------
    AssetValidationError
        If the file fails validation.
    """
    if mime_type not in ALLOWED_MIME_TYPES:
        raise AssetValidationError(
            f"Unsupported file type: {mime_type}. "
            f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}"
        )

    if len(content) > MAX_UPLOAD_BYTES:
        raise AssetValidationError(
            f"File too large: {len(content)} bytes. "
            f"Maximum: {MAX_UPLOAD_BYTES} bytes ({MAX_UPLOAD_BYTES // 1024} KB)."
        )

    if mime_type == "image/svg+xml":
        content = sanitize_svg(content)

    return content


# ---------------------------------------------------------------------------
# Company logo CRUD
# ---------------------------------------------------------------------------


async def set_company_logo(
    session: AsyncSession,
    content: bytes,
    mime_type: str,
    filename: str | None = None,
) -> dict[str, Any]:
    """Upload or replace the company logo.

    1. Validate and sanitize the upload.
    2. Delete the old ``binary_asset`` row (if any).
    3. Insert a new ``binary_asset`` and point ``company.logo_id`` at it.

    The caller is responsible for ``session.commit()``.
    """
    # Validate + sanitize.
    content = validate_upload(content, mime_type, filename)

    company = await get_company(session)
    if company is None:
        raise AssetValidationError("Company profile must be created before uploading a logo.")

    # Delete old asset if replacing.
    if company.logo_id is not None:
        old_asset = await session.get(BinaryAsset, company.logo_id)
        if old_asset is not None:
            await session.delete(old_asset)
            await session.flush()

    # Insert new asset.
    asset = BinaryAsset(
        content=content,
        mime_type=mime_type,
        filename=filename,
        byte_size=len(content),
    )
    session.add(asset)
    await session.flush()  # assign asset.id

    # Link company → new asset.
    company.logo_id = asset.id
    await session.flush()

    # Build logo_url with cache-busting version param (content hash prefix).
    import hashlib

    version = hashlib.sha256(content).hexdigest()[:16]
    logo_url = f"/api/v1/company/logo?v={version}"

    return {
        "logo_url": logo_url,
        "mime_type": mime_type,
        "byte_size": len(content),
    }


async def clear_company_logo(session: AsyncSession) -> bool:
    """Remove the company logo.

    Returns ``True`` if a logo was deleted, ``False`` if there was no logo.
    The caller is responsible for ``session.commit()``.
    """
    company = await get_company(session)
    if company is None or company.logo_id is None:
        return False

    asset = await session.get(BinaryAsset, company.logo_id)
    # The FK ON DELETE SET NULL fires at DB level, but we also set it
    # explicitly to avoid stale ORM state.
    company.logo_id = None

    if asset is not None:
        await session.delete(asset)
    await session.flush()
    return True


async def get_company_logo(
    session: AsyncSession,
) -> tuple[bytes, str] | None:
    """Retrieve the company logo content and mime type.

    Returns ``None`` if the company has no logo.
    """
    company = await get_company(session)
    if company is None or company.logo_id is None:
        return None

    asset = await session.get(BinaryAsset, company.logo_id)
    if asset is None:
        return None

    return (asset.content, asset.mime_type)
