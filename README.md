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

## Later M0 Tracks

These commands become available after the backend, frontend, and deployment
tracks are implemented:

```bash
# Backend (from backend/)
cd backend && uv sync && uv run uvicorn jai.main:app --reload

# Frontend (from frontend/)
cd frontend && npm install && npm run dev

# Deployment
docker compose up --build
# App available at http://localhost:8000
```

## License

See [LICENSE](LICENSE).
