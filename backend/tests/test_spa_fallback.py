"""Tests for SPA fallback static-file hosting in ``jai.main``."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from httpx import ASGITransport, AsyncClient

import jai.config as cfg
from jai.config import Settings
from jai.main import create_app


@contextmanager
def _override_settings(**kwargs: object) -> Generator[None, None, None]:
    """Temporarily replace ``get_settings`` with one using *kwargs*."""
    original = cfg.get_settings
    test_settings = Settings(**kwargs)  # type: ignore[arg-type]
    cfg.get_settings = lambda: test_settings  # type: ignore[assignment]
    try:
        yield
    finally:
        cfg.get_settings = original  # type: ignore[assignment]


def _make_client(tmp_path: Path | None = None) -> AsyncClient:
    """Build an ``AsyncClient`` against a freshly created app."""
    if tmp_path is not None:
        cm = _override_settings(static_dir=str(tmp_path))
    else:
        cm = _override_settings()
    cm.__enter__()  # noqa: PT022 – cleanup handled in the test body via _cleanup
    test_app = create_app()
    transport = ASGITransport(app=test_app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestSPAFallbackWithStatic:
    """SPA fallback when ``static_dir`` is set (deployment mode)."""

    async def test_api_health_still_works(self, tmp_path: Path) -> None:
        """``/api/health`` should return 200 even with SPA hosting active."""
        with _override_settings(static_dir=str(tmp_path)):
            test_app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/api/health")
                assert resp.status_code == 200
                assert resp.json()["status"] == "ok"

    async def test_nonexistent_path_returns_index_html(self, tmp_path: Path) -> None:
        """Any non-API, non-file path should fall back to ``index.html``."""
        (tmp_path / "index.html").write_text("<h1>SPA</h1>")
        with _override_settings(static_dir=str(tmp_path)):
            test_app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/some/spa/route")
                assert resp.status_code == 200
                assert "<h1>SPA</h1>" in resp.text

    async def test_existing_file_served(self, tmp_path: Path) -> None:
        """A real file on disk should be served as-is (not via index.html)."""
        (tmp_path / "index.html").write_text("<h1>SPA</h1>")
        (tmp_path / "readme.txt").write_text("hello world")
        with _override_settings(static_dir=str(tmp_path)):
            test_app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/readme.txt")
                assert resp.status_code == 200
                assert resp.text == "hello world"

    async def test_api_path_not_intercepted(self, tmp_path: Path) -> None:
        """``/api/nonexistent`` should return 404, not index.html."""
        (tmp_path / "index.html").write_text("<h1>SPA</h1>")
        with _override_settings(static_dir=str(tmp_path)):
            test_app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/api/nonexistent")
                assert resp.status_code == 404

    async def test_root_returns_index_html(self, tmp_path: Path) -> None:
        """``/`` (empty path) should serve ``index.html``."""
        (tmp_path / "index.html").write_text("<h1>Root SPA</h1>")
        with _override_settings(static_dir=str(tmp_path)):
            test_app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/")
                assert resp.status_code == 200
                assert "<h1>Root SPA</h1>" in resp.text

    async def test_index_html_is_no_cache(self, tmp_path: Path) -> None:
        """The SPA entry point must be revalidated, never served stale.

        A cached index.html pointing at deleted chunk hashes is exactly the bug
        that makes a redeployed SPA keep running old code in the browser.
        """
        (tmp_path / "index.html").write_text("<h1>SPA</h1>")
        with _override_settings(static_dir=str(tmp_path)):
            test_app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                root = await ac.get("/")
                assert root.headers["cache-control"] == "no-cache"
                spa = await ac.get("/some/spa/route")
                assert spa.headers["cache-control"] == "no-cache"

    async def test_assets_are_immutable(self, tmp_path: Path) -> None:
        """Content-hashed files under ``/assets`` are cached long-term."""
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "index-ABC123.js").write_text("console.log(1)")
        (tmp_path / "index.html").write_text("<h1>SPA</h1>")
        with _override_settings(static_dir=str(tmp_path)):
            test_app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/assets/index-ABC123.js")
                assert resp.status_code == 200
                assert "immutable" in resp.headers["cache-control"]
                assert "max-age=31536000" in resp.headers["cache-control"]


class TestSPAFallbackWithoutStatic:
    """When ``static_dir`` is None (dev mode), no SPA fallback exists."""

    async def test_health_works(self) -> None:
        """``/api/health`` should still work."""
        with _override_settings(static_dir=None):
            test_app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/api/health")
                assert resp.status_code == 200

    async def test_random_path_404(self) -> None:
        """A random non-API path should return 404 (no SPA fallback)."""
        with _override_settings(static_dir=None):
            test_app = create_app()
            async with AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/some/route")
                assert resp.status_code == 404
