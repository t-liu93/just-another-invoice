"""Tests for ``GET /api/health`` endpoint."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    """Happy path: health endpoint returns 200 with expected body."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert isinstance(data["version"], str)


async def test_health_version_matches_settings(client: AsyncClient) -> None:
    """The version field should come from Settings (or default)."""
    from jai.config import get_settings

    settings = get_settings()
    response = await client.get("/api/health")
    assert response.json()["version"] == settings.app_version


async def test_health_is_get_only(client: AsyncClient) -> None:
    """POST/PUT/DELETE to /api/health should return 405 Method Not Allowed."""
    for method in ("post", "put", "delete", "patch"):
        resp = await getattr(client, method)("/api/health")
        assert resp.status_code == 405, f"{method.upper()} should be 405, got {resp.status_code}"


async def test_openapi_schema_uses_contract_path(client: AsyncClient) -> None:
    """OpenAPI must be exposed at the contract path used by frontend codegen."""
    response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Just Another Invoice"
