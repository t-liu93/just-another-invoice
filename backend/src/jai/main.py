"""FastAPI application assembly, route mounting, and SPA fallback."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import jai.config as _cfg
from jai.api.health import router as health_router


def create_app() -> FastAPI:
    """Application factory.

    In development the frontend is served by Vite on port 5173 and
    ``static_dir`` is ``None``.  In the production single-container
    deployment ``static_dir`` points to the frontend build output.
    """
    settings = _cfg.get_settings()

    app = FastAPI(
        title="Just Another Invoice",
        version=settings.app_version,
        openapi_url="/api/v1/openapi.json",
    )

    # -- Dev CORS -----------------------------------------------------------
    # Allow the Vite dev server (port 5173) to call the API.
    if settings.static_dir is None:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # -- API routes ---------------------------------------------------------
    app.include_router(health_router)

    # -- Static files + SPA fallback (deployment mode) ----------------------
    if settings.static_dir is not None:
        dist = Path(settings.static_dir).resolve()
        if dist.is_dir():
            # Mount assets sub-directory for cache-busted static files.
            assets = dist / "assets"
            if assets.is_dir():
                app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

            # Catch-all SPA fallback – must be registered *after* all API
            # routes so that ``/api/*`` paths are never shadowed.
            @app.get("/{full_path:path}", include_in_schema=False)
            async def spa_fallback(full_path: str) -> FileResponse:
                # Never intercept API paths.
                if full_path.startswith("api/"):
                    raise HTTPException(status_code=404)
                candidate = (dist / full_path).resolve()
                # Guard against path-traversal.
                if candidate.is_relative_to(dist) and candidate.is_file():
                    return FileResponse(candidate)
                return FileResponse(dist / "index.html")

    return app


# Module-level instance for ``uvicorn jai.main:app``.
app = create_app()
