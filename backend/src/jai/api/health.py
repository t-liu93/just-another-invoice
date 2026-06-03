"""Health-check endpoint: ``GET /api/health``."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

import jai.config as _cfg

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Response body for the health-check endpoint."""

    status: str
    version: str


@router.get("/api/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return application status and version."""
    settings = _cfg.get_settings()
    return HealthResponse(status="ok", version=settings.app_version)
