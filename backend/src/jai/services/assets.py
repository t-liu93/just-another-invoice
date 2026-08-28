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
- SVG sanitization uses a two-step approach (red-line 7):
  (0) Pre-processing: ``_inline_svg_styles`` inlines CSS ``<style>`` rules as
      presentation attributes on matched elements, then removes ``<style>``
      elements.  This preserves colours/strokes defined only in CSS classes
      (common in Illustrator/Figma exports) while keeping the output free of
      ``<style>`` blocks.  Only safe paint/stroke/opacity attributes are
      inlined; any value containing an external ``url(...)`` is discarded.
  (1) ``nh3`` (Ammonia) strips ``<script>``, ``<style>`` (belt-and-suspenders),
      event handlers (``on*``), unapproved tags/attributes, and comments.
  (2) Post-processing strips ``href``/``xlink:href`` attributes that
      reference non-local URLs (preventing SSRF via external references).
  (3) Post-processing strips external ``url()`` references from paint
      attributes (``fill``, ``stroke``), keeping only ``url(#id)``.
  (4) ``style`` attribute excluded from allow-list (CSS ``url()`` risk).
  This fully satisfies red-line 7 (XSS/SSRF prevention).
- ``company.logo_id`` is managed here; the FK ``ON DELETE SET NULL`` ensures
  the company row stays valid when a binary_asset is deleted.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

import cssselect2
import nh3
import tinycss2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jai.db import set_rls_company
from jai.models.binary_asset import BinaryAsset
from jai.models.document import InvoicePartySnapshot
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

#: SVG XML namespace URI.
_SVG_NS: str = "http://www.w3.org/2000/svg"

#: Safe CSS presentation attributes that may be inlined from ``<style>`` rules.
#: This whitelist excludes anything that could embed external references or
#: execute code.  ``url()`` values are additionally filtered at runtime.
_SAFE_INLINE_ATTRS: frozenset[str] = frozenset(
    {
        "fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
        "stroke-linejoin", "stroke-miterlimit", "stroke-opacity", "fill-opacity",
        "fill-rule", "clip-rule", "opacity", "color", "stop-color", "stop-opacity",
    }
)

#: Detects external ``url()`` references (anything that is not ``url(#...)``)
#: in CSS declaration values.  Used to discard SSRF-risky values during style
#: inlining.
_CSS_EXT_URL_RE: re.Pattern[str] = re.compile(
    r"url\s*\(\s*(?!#)",
    re.IGNORECASE,
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
#:
#: The full set of safe paint/stroke/opacity presentation attributes is
#: included here so that ``_inline_svg_styles`` inlined values survive the
#: subsequent ``nh3`` pass without being stripped.
_SHAPE_PAINT_ATTRS: frozenset[str] = frozenset(
    {
        "fill", "fill-opacity", "fill-rule",
        "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
        "stroke-linejoin", "stroke-miterlimit", "stroke-opacity",
        "clip-rule", "opacity", "color",
    }
)

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
    "g": frozenset({"transform"} | _SHAPE_PAINT_ATTRS),
    "path": frozenset({"d", "transform"} | _SHAPE_PAINT_ATTRS),
    "circle": frozenset({"cx", "cy", "r", "transform"} | _SHAPE_PAINT_ATTRS),
    "ellipse": frozenset({"cx", "cy", "rx", "ry", "transform"} | _SHAPE_PAINT_ATTRS),
    "line": frozenset({"x1", "y1", "x2", "y2", "transform"} | _SHAPE_PAINT_ATTRS),
    "rect": frozenset(
        {"x", "y", "width", "height", "rx", "ry", "transform"} | _SHAPE_PAINT_ATTRS
    ),
    "polygon": frozenset({"points", "transform"} | _SHAPE_PAINT_ATTRS),
    "polyline": frozenset({"points", "transform"} | _SHAPE_PAINT_ATTRS),
    "text": frozenset(
        {
            "x", "y", "dx", "dy", "font-size", "font-family",
            "text-anchor", "dominant-baseline", "transform",
        }
        | _SHAPE_PAINT_ATTRS
    ),
    "tspan": frozenset(
        {"x", "y", "dx", "dy", "font-size", "font-family"} | _SHAPE_PAINT_ATTRS
    ),
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

# Register the SVG namespace so ET serialises without "ns0:" prefixes.
ET.register_namespace("", _SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


def _inline_svg_styles(svg_text: str) -> str:
    """Inline CSS ``<style>`` rules as presentation attributes on SVG elements.

    Many vector-editing tools (Illustrator, Figma, Inkscape) export SVGs
    where all colour/stroke information lives in a ``<style>`` block as CSS
    class rules (e.g. ``.cls-2 { fill: #3a3a3a }``), with elements carrying
    only ``class="cls-N"`` attributes and no inline ``fill``/``stroke``.
    When the subsequent ``nh3`` pass strips ``<style>`` (as required by
    red-line 7), all those elements fall back to the SVG default
    (``fill: black``), producing a solid-black silhouette.

    This pre-processing pass resolves that by "compiling" each CSS rule into
    equivalent presentation attributes on every matched element **before**
    ``nh3`` runs, so the output is both style-free and correctly coloured.

    Safety
    ------
    - Only attributes in ``_SAFE_INLINE_ATTRS`` are inlined.
    - Any declaration whose serialised value contains an external ``url(...)``
      (i.e. anything other than ``url(#local-id)``) is silently discarded,
      preventing SSRF.
    - ``<style>`` elements are removed from the tree after inlining; ``nh3``
      also removes them (belt-and-suspenders).
    - The entire function is wrapped in a broad ``except`` block: any parse
      or matching error returns the original text unchanged, so this pass
      degrades gracefully on malformed or unusual SVGs.

    Parameters
    ----------
    svg_text:
        Raw SVG as a Unicode string.

    Returns
    -------
    SVG string with ``<style>`` rules inlined as presentation attributes and
    ``<style>`` elements removed, or the original string if processing fails.
    """
    try:
        root = ET.fromstring(svg_text)  # noqa: S314 — input will be sanitised by nh3 next

        # Collect all <style> text, whether namespaced or not.
        style_texts: list[str] = []
        for elem in root.iter():
            if elem.tag in (f"{{{_SVG_NS}}}style", "style"):
                if elem.text:
                    style_texts.append(elem.text)

        if not style_texts:
            return svg_text  # Nothing to inline; skip the round-trip.

        combined_css = " ".join(style_texts)

        # Parse CSS into rules; skip whitespace/comment tokens.
        rules = tinycss2.parse_stylesheet(
            combined_css, skip_whitespace=True, skip_comments=True
        )

        # Build (selector_str, {prop: value}) list in source order so that
        # later / more specific rules correctly override earlier ones.
        decl_rules: list[tuple[str, dict[str, str]]] = []
        for rule in rules:
            if rule.type != "qualified-rule":
                continue
            selector_str = tinycss2.serialize(rule.prelude).strip()
            if not selector_str:
                continue
            decls = tinycss2.parse_declaration_list(
                rule.content, skip_whitespace=True
            )
            props: dict[str, str] = {}
            for decl in decls:
                if decl.type != "declaration":
                    continue
                name = decl.name.lower()
                if name not in _SAFE_INLINE_ATTRS:
                    continue
                value = tinycss2.serialize(decl.value).strip()
                # Discard any value that references an external URL (SSRF).
                if _CSS_EXT_URL_RE.search(value):
                    continue
                props[name] = value
            if props:
                decl_rules.append((selector_str, props))

        if not decl_rules:
            return svg_text  # No safe rules to apply.

        # Use cssselect2 to match selectors against the SVG DOM.
        wrapper = cssselect2.ElementWrapper.from_xml_root(root)

        for selector_str, props in decl_rules:
            try:
                matches = wrapper.query_all(selector_str)
            except Exception:  # noqa: BLE001 — unsupported selector etc.
                continue
            for match in matches:
                elem = match.etree_element
                for prop, val in props.items():
                    # CSS rules override existing presentation attributes
                    # (mirrors browser cascade behaviour).
                    elem.set(prop, val)

        # Remove <style> elements from the tree (nh3 will also remove them).
        parent_map = {child: parent for parent in root.iter() for child in parent}
        for elem in list(root.iter()):
            if elem.tag in (f"{{{_SVG_NS}}}style", "style"):
                parent = parent_map.get(elem)
                if parent is not None:
                    parent.remove(elem)

        return ET.tostring(root, encoding="unicode")

    except Exception:  # noqa: BLE001 — malformed SVG, namespace issues, etc.
        # Fail open: return the original text so the nh3 pass still runs.
        return svg_text


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

    Four-pass approach:
    0. Pre-processing (``_inline_svg_styles``): CSS ``<style>`` rules are
       inlined as presentation attributes so that logo colours survive the
       subsequent ``<style>``-stripping step.  Only safe paint/stroke/opacity
       attributes are inlined; external ``url()`` values are discarded.
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

    # Pass 0: inline <style> CSS rules as presentation attributes so colours
    # are preserved after nh3 strips the <style> block.
    text_content = _inline_svg_styles(text_content)

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

    # An issued document owns an immutable reference to its issue-time logo.
    # Keep a referenced asset while allowing ordinary logo replacement/clear.
    if company.logo_id is not None:
        await set_rls_company(session, company.id)
        old_asset = await session.get(BinaryAsset, company.logo_id)
        snapshot_uses = await session.scalar(
            select(InvoicePartySnapshot.id).where(
                InvoicePartySnapshot.logo_id == company.logo_id
            ).limit(1)
        )
        if old_asset is not None and snapshot_uses is None:
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

    old_logo_id = company.logo_id
    asset = await session.get(BinaryAsset, old_logo_id)
    await set_rls_company(session, company.id)
    # The FK ON DELETE SET NULL fires at DB level, but we also set it
    # explicitly to avoid stale ORM state.
    company.logo_id = None

    snapshot_uses = await session.scalar(
        select(InvoicePartySnapshot.id).where(
            InvoicePartySnapshot.logo_id == old_logo_id
        ).limit(1)
    )
    if asset is not None and snapshot_uses is None:
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
