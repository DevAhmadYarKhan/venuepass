# VenuePass API

Event ticket reservation API built with Python 3.14, FastAPI, async SQLAlchemy,
PostgreSQL, Alembic, and uv. This initial scaffold provides health checking and
database infrastructure; no application models or migration revisions are defined yet.

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

On an empty database this reports no revision. The existing local databases already
contain `events` and `alembic_version` tables: `venuepass_db` references revision
`20260925_0001`, and `venuepass_db_test` references `20260926_0002`. Those migration
files are absent from this repository, so `alembic current` currently reports
"Can't locate revision". Recover the matching migration history before managing
these existing schemas; do not stamp or reset them without deciding how to preserve
their data. The scaffold does not alter these tables.

When adding
models, subclass `app.database.Base` and import the model modules in `alembic/env.py`
so autogeneration sees their metadata. Then generate, review, and apply a migration:

```bash
uv run alembic revision --autogenerate -m "Describe schema change"
uv run alembic upgrade head
```

Alembic uses `DATABASE_URL`. To target the test database, explicitly override that
environment variable with its URL when running migration commands.

## Tests

```bash
uv run pytest
uv run pytest -m "not integration"
```

The integration test executes read-only queries against `TEST_DATABASE_URL` and
requires its database name to be `venuepass_db_test`. It never falls back to the
application database. Unit tests can run without PostgreSQL.
