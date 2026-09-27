# VenuePass API

Event ticket reservation API built with Python 3.14, FastAPI, async SQLAlchemy,
PostgreSQL, Alembic, and uv. Supports health checking, event creation, and event
browsing. Authentication and ticket reservations are not implemented yet.

## Local setup

Install uv and ensure PostgreSQL is running on localhost:5432. The local databases
are `venuepass_db` and `venuepass_db_test`, owned by `venuepass_user`.

```bash
uv sync --locked
cp .env.example .env  # Only on a fresh checkout; preserve an existing .env.
```

Replace `YOUR_PASSWORD` in `.env` with the database role's password. Settings load
the repository-root `.env`; environment variables take precedence. `DATABASE_URL`
is required; `TEST_DATABASE_URL` is required only for integration tests. `.env` is
ignored by Git. Keep real credentials out of committed files.

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

- Health: <http://127.0.0.1:8000/health> returns `{"status":"ok"}`.
- Interactive API docs: <http://127.0.0.1:8000/docs>.

Health checks confirm the application is running and do not connect to PostgreSQL.
The application creates no tables and runs no migrations at startup.

## Database and migrations

`app.database.get_session` is the request-scoped async session dependency. Callers
explicitly commit writes; the dependency closes the session and rolls back any
uncommitted transaction when the request ends. The engine is disposed at shutdown.

```bash
uv run alembic current
```

The initial revision `20260927_0001` creates the events table. Apply it with
`uv run alembic upgrade head`; `current` then reports this revision as the head.

When adding models, subclass `app.database.Base` and import their modules in `alembic/env.py`
so autogeneration sees their metadata. Then generate, review, and apply a migration:

```bash
uv run alembic revision --autogenerate -m "Describe schema change"
uv run alembic upgrade head
```

Alembic uses `DATABASE_URL`. To target the test database, explicitly override that
environment variable with its URL when running migration commands.

## Events API

`POST /events` creates an event and returns HTTP 201 with its UUID, submitted
fields, and database-generated `created_at`. `name` and `venue` are trimmed and
must contain 1–255 characters. `capacity` must be an integer from 1 to 2147483647.
`starts_at` must include a timezone and be in the future. Optional `ends_at` must
include a timezone and be strictly later than `starts_at`; omitted or null means
no fixed finish time. `description` is optional. Invalid input returns HTTP 422.

Choose future dates when trying this example:

```bash
curl -X POST http://127.0.0.1:8000/events \
  -H 'Content-Type: application/json' \
  -d '{"name":"Python meetup","venue":"Main hall","description":"An evening of talks","starts_at":"2099-10-01T18:00:00Z","ends_at":"2099-10-01T20:00:00Z","capacity":50}'

curl 'http://127.0.0.1:8000/events?limit=20&offset=0'
curl 'http://127.0.0.1:8000/events/REPLACE_WITH_EVENT_UUID'
```

`GET /events` returns an array ordered by start time, then UUID, including past
events. `limit` defaults to 20 (range 1–100); `offset` defaults to 0 and must be
nonnegative. `GET /events/{id}` returns one event, HTTP 404 for an unknown UUID,
or HTTP 422 for a malformed UUID. Responses always include nullable `ends_at`.
These endpoints are open for the local prototype; there is no authentication yet.

## Tests

```bash
uv run pytest
uv run pytest -m "not integration"
```

Before integration tests, migrate the test database using the URL from your
local `.env` (the command prompts for it to avoid storing credentials in shell history):

```bash
read -r -s -p 'Test database URL: ' venuepass_test_url
DATABASE_URL="$venuepass_test_url" uv run alembic upgrade head
unset venuepass_test_url
```

Integration tests require an otherwise empty, migrated `venuepass_db_test` and
explicitly use `TEST_DATABASE_URL`; they never fall back to the application
database. Endpoint tests write inside an outer transaction rolled back after
each test, even when an endpoint commits. Unit tests can run without PostgreSQL.
