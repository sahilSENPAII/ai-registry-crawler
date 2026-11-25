# ai-registry-crawler

Crawls an AWS account, finds AI systems, stores them in a Postgres AI registry,
traces agent behaviour, and shows an executive dashboard.

**Status: Phase 1 — project skeleton only** (FastAPI + Postgres + Next.js, wired together).

## Run everything

```bash
docker compose up --build
```

- Web: http://localhost:3000
- API health: http://localhost:8000/health
- Postgres: `localhost:5433` (user/password/db: `registry`)

## Run the backend locally

```bash
docker compose up db            # just Postgres, on host port 5433
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.api:app --reload    # DATABASE_URL defaults to localhost:5433
pytest
```

Frontend locally: `cd frontend && npm install && npm run dev`.

Copy `.env.example` to `.env` to see/override every setting.

## Troubleshooting

**Port clash (`port is already allocated` / `address already in use`).**
Postgres is mapped to host port **5433**, not 5432, so it doesn't clash with a
Postgres already running on your machine. If 5433, 8000 or 3000 is taken, find the
culprit with `lsof -i :5433` and stop it, or change the left side of the port
mapping in `docker-compose.yml` (and `DATABASE_URL` for local runs).

**API starts before the DB is ready.**
Compose waits for the `db` healthcheck (`pg_isready`) before starting `api`, and the
API itself retries `SELECT 1` up to 10 times (2s apart) on startup, logging each
retry. If the DB still isn't up, the API starts anyway and `/health` returns
`{"status":"degraded","db":"<error>"}` instead of crashing. Check `docker compose logs db`.

**`localhost` vs `db` inside docker.**
Inside a container, `localhost` is the container itself. The API container must use
`db:5432` (service name + container port); that's set in `docker-compose.yml`.
From your host machine use `localhost:5433`. The browser runs on the host, so the
frontend calls the API at `http://localhost:8000`.
