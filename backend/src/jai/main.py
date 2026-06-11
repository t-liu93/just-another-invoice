"""FastAPI application assembly, route mounting, and SPA fallback."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

import jai.config as _cfg
from jai.api.auth import router as auth_router
from jai.api.auth import users_router
from jai.api.company import router as company_router
from jai.api.customers import router as customers_router
from jai.api.dictionary import router as dictionary_router
from jai.api.health import router as health_router
from jai.api.invoices import router as invoices_router
from jai.api.products import router as products_router
from jai.api.quotes import router as quotes_router
from jai.api.settings import router as settings_router
from jai.api.vat import router as vat_router
from jai.auth.secret import resolve_auth_secret
from jai.db import get_engine, get_session_maker
from jai.startup import check_db_migration

logger = logging.getLogger("jai")

# index.html is the SPA entry point: it is NOT content-hashed, so it must never
# be served from the browser cache without revalidation — otherwise a cached
# index.html keeps pointing at stale (deleted) chunk hashes after a redeploy,
# and the user runs old code indefinitely. ``no-cache`` forces an ETag
# revalidation on every load (cheap 304 when unchanged).
_HTML_CACHE_CONTROL = "no-cache"
# Everything under /assets is content-hash-named by Vite, so it is safe to cache
# forever and never revalidate.
_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


class _ImmutableStaticFiles(StaticFiles):
    """StaticFiles that marks content-hashed assets immutable for one year."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = _ASSET_CACHE_CONTROL
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan – runs startup checks, then yields."""
    # -- Startup: fail-closed migration check --------------------------------
    engine = get_engine()
    await check_db_migration(engine)
    # -- Startup: resolve the JWT signing secret -----------------------------
    # Env override wins; otherwise an auto-generated secret is loaded from /
    # persisted to the DB (first boot).  Runs after the migration check so
    # the ``setting`` table is guaranteed to exist.
    await resolve_auth_secret(get_session_maker())
    yield


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
        lifespan=lifespan,
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
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(company_router)
    app.include_router(customers_router)
    app.include_router(settings_router)
    app.include_router(vat_router)
    app.include_router(dictionary_router)
    app.include_router(products_router)
    app.include_router(invoices_router)
    app.include_router(quotes_router)

    # -- Static files + SPA fallback (deployment mode) ----------------------
    if settings.static_dir is not None:
        dist = Path(settings.static_dir).resolve()
        if dist.is_dir():
            # Mount assets sub-directory for cache-busted static files.
            assets = dist / "assets"
            if assets.is_dir():
                app.mount(
                    "/assets",
                    _ImmutableStaticFiles(directory=str(assets)),
                    name="assets",
                )

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
                    # HTML entry points must revalidate; other top-level static
                    # files (favicon, etc.) are fine to revalidate too.
                    return FileResponse(
                        candidate,
                        headers={"Cache-Control": _HTML_CACHE_CONTROL},
                    )
                return FileResponse(
                    dist / "index.html",
                    headers={"Cache-Control": _HTML_CACHE_CONTROL},
                )

    return app


# Module-level instance for ``uvicorn jai.main:app``.
app = create_app()
