# Just Another Invoice

Self-hosted invoicing application for Dutch freelancers and small businesses.

Built with FastAPI + Vue 3 + PostgreSQL.

## Quick Start (Track 0)

```bash
# Copy environment template
cp .env.example .env

# Start PostgreSQL for local development; the dev compose file binds only 127.0.0.1
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres

# Verify the database responds
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1 AS connected;"'
```

Docker Compose reads `.env` automatically. PostgreSQL always listens on `5432`
inside the container. The base `docker-compose.yml` does not publish the database
port; `docker-compose.dev.yml` binds `127.0.0.1:${POSTGRES_DEV_PORT:-5433}` for
local tools and the backend `DATABASE_URL`.

To use a different local dev port:

```bash
cp .env.dev.example .env.dev
# Edit POSTGRES_DEV_PORT, and keep the port in DATABASE_URL in .env in sync.
docker compose --env-file .env --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d postgres
```

## Ports

- Production Compose: the app container listens on `8000` internally and is
  published only as `127.0.0.1:${APP_HOST_PORT:-12000}:8000` for Nginx or another
  local reverse proxy. PostgreSQL is not published to the host; app containers
  reach it on the Compose network as `postgres:5432`.
- Split development: start the backend manually on port `8000` with
  `uv run uvicorn jai.main:app --reload --port 8000`. The Vite dev proxy and
  OpenAPI codegen use that fixed backend port by default.

## Later M0 Tracks

These commands become available after the backend, frontend, and deployment
tracks are implemented:

```bash
# Backend (from backend/)
cd backend && uv sync
uv run uvicorn jai.main:app --reload --port 8000

# Frontend (from frontend/)
cd frontend && npm install && npm run dev

# Local single-container integration build
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
# App available at http://localhost:${APP_HOST_PORT:-12000}

# Production deployment after an image has been published to GHCR
docker compose up -d
```

## License

See [LICENSE](LICENSE).
