# syntax=docker/dockerfile:1

# --- Stage 1: frontend builder -------------------------------------------
# Builds the Vite SPA. No backend needed: src/api/schema.d.ts is committed,
# so `vite build` is self-contained.
FROM node:22-bookworm-slim AS frontend
WORKDIR /app/frontend
# .npmrc carries `legacy-peer-deps=true` (typescript 6 vs openapi-typescript's
# peer ^5). It MUST be copied before `npm ci`, or the install reverts to strict
# peer resolution and fails.
COPY frontend/package.json frontend/package-lock.json* frontend/.npmrc ./
RUN npm ci
COPY frontend/ ./
RUN npm run build            # vue-tsc -b && vite build -> /app/frontend/dist

# --- Stage 2: backend deps (uv) ------------------------------------------
# Installs ONLY third-party runtime deps into /app/.venv (no dev group, project
# itself not installed — it's resolved via PYTHONPATH in the runtime stage).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS backend-deps
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --- Stage 3: runtime ----------------------------------------------------
# Slim image: the venv + source + built SPA. No uv, no Node, no build tools.
# DATABASE_URL is NOT hardcoded — it is assembled at runtime from POSTGRES_*
# env vars (see jai.config.Settings).
FROM python:3.12-slim-bookworm AS runtime
# WeasyPrint system libraries (D1 – M9 PDF rendering) + Noto fonts for
# full Unicode/CJK coverage.  Cleaned in the same layer to keep image size down.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        libcairo2 \
        shared-mime-info \
        fonts-noto-core \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    STATIC_DIR=/app/static
WORKDIR /app
COPY --from=backend-deps /app/.venv /app/.venv
COPY backend/src/ ./src/
COPY backend/alembic/ ./alembic/
COPY backend/alembic.ini ./alembic.ini
COPY --from=frontend /app/frontend/dist/ ./static/
COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
# CLI wrapper so operators can run `docker compose run --rm app jai <cmd>`
# (e.g. `jai set-password <email>`).  The project itself is not pip-installed
# in this image — it runs via PYTHONPATH — so we invoke it as a module.
RUN printf '#!/bin/sh\nexec python -m jai.cli "$@"\n' > /usr/local/bin/jai \
    && chmod +x /usr/local/bin/jai
EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "jai.main:app", "--host", "0.0.0.0", "--port", "8000"]
