# preview-example-backend

A small FastAPI notes API for preview-hub, using Python 3.12 and PostgreSQL 16.
Synthetic example data only. MIT licensed.

## Run locally

Install Python 3.12, uv, and Docker, then start a disposable local database:

```sh
docker run --name preview-notes-db -d -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=notes -e POSTGRES_PASSWORD=notes -e POSTGRES_DB=notes postgres:16
uv sync
export DATABASE_URL='postgresql+psycopg://notes:notes@localhost:5432/notes'
export CORS_ORIGINS='http://localhost:3000'
# Once PostgreSQL is ready:
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-config app/logging.json
```

`CORS_ORIGINS` is a comma-separated list; empty or unset allows no cross-origin
browser access. `DATABASE_URL` is required and uses `postgresql+psycopg://`.
Migrations and seeds run explicitly, separately from server startup.

Optionally set `NOTIFIER_URL` (for example, `http://localhost:8001`). After a
note is committed, a background task sends one POST to `${NOTIFIER_URL}/api/notify`
with `{"event":"note.created","payload":{"id":"<uuid>","text":"<note text>"}}`
and a 2-second timeout. Delivery is best-effort, without retries: errors are
logged once and do not change the 201 response. Empty or unset disables the call.

- `GET /healthz`: `200 {"status":"ok"}` after a database query; otherwise 503.
- `GET /api/notes`: JSON array, newest creation timestamp first (UUID breaks ties).
- `POST /api/notes`: `{"text":"My note"}` creates a note and returns 201 with
  `{id, text, created_at}`. Text must have 1–500 characters; invalid input returns 422.
  IDs are UUIDs and timestamps include the timezone. Duplicate text is allowed.

```sh
curl -H 'Content-Type: application/json' -d '{"text":"Hello preview"}' \
  http://localhost:8000/api/notes
```

## Preview environments

`preview.yaml` follows preview-hub contract C1. The hub builds the Dockerfile,
provides a PostgreSQL 16 resource, injects its URL into `DATABASE_URL`, and injects
the frontend public URL into `CORS_ORIGINS`. It runs `alembic upgrade head` followed
by `python -m app.seed` using this image before starting the backend on port 8000.
Those commands also run on backend updates against the retained database volume.
The seed inserts two fixed UUIDs with `ON CONFLICT DO NOTHING`, so reruns preserve
existing notes and timestamps. The `api` subdomain exposes the service and
`/healthz` gates readiness. This manifest expects frontend in the environment.
The notifier dependency is optional; the hub injects its internal URL into
`NOTIFIER_URL` when present and omits the variable when absent.

The multi-stage image runs as UID 10001, logs to stdout, and uses a replaceable
CMD so both resource initialization commands work without an entrypoint wrapper.

```sh
docker build -t preview-example-backend .
# Supply DATABASE_URL reachable from the container network:
docker run --rm -e DATABASE_URL preview-example-backend alembic upgrade head
docker run --rm -e DATABASE_URL preview-example-backend python -m app.seed
```

## Verification

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
export TEST_DATABASE_URL='postgresql+psycopg://notes:notes@localhost:5432/notes'
uv run pytest -ra
docker build -t preview-example-backend .
```

Database tests use real PostgreSQL, run migrations twice, and create/drop a unique
schema per test. The test role needs schema creation permission; use a dedicated
test database. When `TEST_DATABASE_URL` is unset, database tests explicitly skip;
unit tests still run. CI supplies PostgreSQL 16 and runs all checks on PRs and
pushes to main. Dependency declarations are in `pyproject.toml`; committed `uv.lock` pins the
versions installed by Docker. CI uses `uv sync --locked` to reject lock drift.
