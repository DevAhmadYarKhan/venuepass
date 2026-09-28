# VenuePass API

Event ticket reservation API built with Python 3.14, FastAPI, async SQLAlchemy,
PostgreSQL, Alembic, and uv. Supports health checking, event creation, and event
browsing, plus local user registration and JWT authentication. Ticket reservations
are not implemented yet.

## Application structure

- `app/routers/` defines HTTP endpoints and translates application errors into
  status codes and responses.
- `app/schemas/` defines request validation and public response models.
- `app/services/` handles account and event operations, database queries, and
  write transactions. Services receive sessions and explicit inputs rather than
  HTTP requests or application state.
- `app/security.py` handles password hashing and JWT creation/validation;
  `app/dependencies.py` resolves bearer credentials into the current user.
- `app/errors.py` defines application exceptions independent of HTTP.
- `app/main.py` assembles routers, manages resource startup/shutdown, and registers
  the validation-error handler. Models, settings, and database session management
  remain in their dedicated modules.

## Local setup

Install uv and ensure PostgreSQL is running on localhost:5432. The local databases
are `venuepass_db` and `venuepass_db_test`, owned by `venuepass_user`.

```bash
uv sync --locked
cp .env.example .env  # Only on a fresh checkout; preserve an existing .env.
```

Replace `YOUR_PASSWORD` in `.env` with the database role's password. Settings load
the repository-root `.env`; environment variables take precedence. `DATABASE_URL`
and `JWT_SECRET` are required; `TEST_DATABASE_URL` is required only for integration tests. `.env` is
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

Revision `20260927_0001` creates events; `20260927_0002` adds users. Apply both
with `uv run alembic upgrade head`; `current` reports `20260928_0005` as the head.
Revision `20260928_0005` adds physical venue seats without modifying existing data.
Revision `20260928_0004` adds venues and venue-manager permission, preserving existing
users and events. Downgrading that revision removes venue records and the new flag.

Revision `20260928_0003` adds organizer permission and required event ownership.
It deletes existing event records because their creators were not recorded, while
preserving user accounts. Downgrading cannot restore the deleted events.

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
fields, the authenticated user’s `organizer_id`, and database-generated `created_at`. `name` and `venue` are trimmed and
must contain 1–255 characters. `capacity` must be an integer from 1 to 2147483647.
`starts_at` must include a timezone and be in the future. Optional `ends_at` must
include a timezone and be strictly later than `starts_at`; omitted or null means
no fixed finish time. `description` is optional. Invalid input returns HTTP 422.

Choose future dates when trying this example:

```bash
curl -X POST http://127.0.0.1:8000/events \
  -H 'Authorization: Bearer REPLACE_WITH_ORGANIZER_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"name":"Python meetup","venue":"Main hall","description":"An evening of talks","starts_at":"2099-10-01T18:00:00Z","ends_at":"2099-10-01T20:00:00Z","capacity":50}'

curl 'http://127.0.0.1:8000/events?limit=20&offset=0'
curl 'http://127.0.0.1:8000/events/REPLACE_WITH_EVENT_UUID'
```

`GET /events` returns an array ordered by start time, then UUID, including past
events. `limit` defaults to 20 (range 1–100); `offset` defaults to 0 and must be
nonnegative. `GET /events/{id}` returns one event, HTTP 404 for an unknown UUID,
or HTTP 422 for a malformed UUID. Responses always include nullable `ends_at`.
Event browsing remains public. Creation requires a valid bearer token (HTTP 401
otherwise) and organizer permission (HTTP 403 for ordinary users). Request input
cannot override `organizer_id`.

Register an account, then grant organizer permission locally:

```bash
uv run python -m app.cli promote-organizer user@example.com
```

This command uses `DATABASE_URL`, normalizes the email, and succeeds if the account
is already an organizer. An unknown account returns a nonzero exit code. Existing
login tokens work immediately after promotion because permission is read from the
database. Users who own events cannot be deleted while those events reference them.
New and existing accounts default to `is_organizer: false`; public registration
cannot grant this permission. User responses include `is_organizer`.

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
each test, even when an endpoint commits. The CLI and concurrent-registration tests use
independent committed transactions and delete their uniquely named accounts afterward.
Unit tests can run without PostgreSQL.

## Registration and authentication

Set `JWT_SECRET` in your ignored `.env` to a random signing key of at least 32
characters. Generate one with `uv run python -c "import secrets; print(secrets.token_hex(32))"`.
Use a different secret for each environment. Changing it invalidates existing tokens.

- `POST /auth/register` accepts JSON `email` and `password`, returning HTTP 201
  with `id`, `email`, `is_organizer`, `is_venue_manager`, and `created_at`. Emails are validated, trimmed, and normalized
  to lowercase. Duplicate emails return HTTP 409. Passwords require 15–128
  characters, are preserved exactly, and are stored as Argon2 hashes.
- `POST /auth/login` accepts the same JSON fields and returns `access_token`,
  `token_type: "bearer"`, and `expires_in: 1800`. Invalid credentials return HTTP 401.
- `GET /users/me` requires `Authorization: Bearer <access_token>` and returns the
  account's public fields. Invalid or expired tokens return HTTP 401.

In `/docs`, call `/auth/login` with **Try it out**, copy `access_token`, then click
**Authorize** and paste it. This uses HTTP Bearer authentication, not OAuth2.

Registration permits immediate login; email ownership is not verified. Tokens
expire after 30 minutes, at which point users log in again. Refresh, server-side
logout/revocation, password reset, and venue staff permissions are deferred. Passwords and hashes
are excluded from public responses, and validation errors omit submitted values.

## Venues

Venue management is independent of event organization. Existing and new users
start with `is_venue_manager: false`; registration cannot grant either permission.
A user may have one permission, both, or neither. Grant venue creation locally:

```bash
uv run python -m app.cli promote-venue-manager user@example.com
```

The command uses `DATABASE_URL`, normalizes and validates the email, and requires
an existing account. Repeating promotion succeeds; missing accounts return a
nonzero exit code. Existing login tokens work after promotion. The organizer
promotion command remains available and does not grant venue permission.

- `POST /venues` requires venue-manager permission: HTTP 401 without valid
  authentication, HTTP 403 without permission, HTTP 201 on success.
- Input contains `name` (1–255 characters) and `address` (1–1,000 characters).
  Both are trimmed and must be nonblank. Invalid input returns HTTP 422.
- Responses contain `id`, `name`, `address`, `owner_id`, and `created_at`.
  Ownership always comes from the authenticated user; submitted owner IDs are
  ignored. Venue names need not be unique.
- `GET /venues` is public and returns an array ordered by creation time, then UUID.
  `limit` defaults to 20 (range 1–100); `offset` defaults to 0 and is nonnegative.
- `GET /venues/{id}` is public, returning HTTP 404 for unknown UUIDs and HTTP 422
  for malformed UUIDs. Owners cannot be deleted while venues reference them.

```bash
curl -X POST http://127.0.0.1:8000/venues \
  -H 'Authorization: Bearer REPLACE_WITH_VENUE_MANAGER_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"name":"Main Hall","address":"1 High Street, London"}'
curl 'http://127.0.0.1:8000/venues?limit=20&offset=0'
curl 'http://127.0.0.1:8000/venues/REPLACE_WITH_VENUE_UUID'
```

Editing, deletion, shared staff access, and event-to-venue relationships
are deferred. Events continue to use their existing free-text venue field.

## Venue seats

Venue owners with current venue-manager permission can add seats in atomic batches:

```bash
curl -X POST http://127.0.0.1:8000/venues/VENUE_UUID/seats \
  -H 'Authorization: Bearer OWNER_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"seats":[{"section":"Main","row":"A","number":1},{"section":"Main","row":"A","number":2}]}'
curl 'http://127.0.0.1:8000/venues/VENUE_UUID/seats?limit=100&offset=0'
```

`POST /venues/{venue_id}/seats` accepts 1–500 explicit seats and returns HTTP 201
with an array in request order. Each response includes `id`, `venue_id`, `section`,
`row`, and `number`. Section and row are trimmed, case-sensitive labels of 1–100
characters. Number is an integer from 1 to 2147483647. Venue membership comes from
the URL. The same section/row/number cannot appear twice within a venue.

A duplicate within the batch or already stored returns HTTP 409 and inserts none
of that batch. This also applies to concurrent overlapping requests. Invalid
input returns HTTP 422, missing authentication 401, missing permission or ownership
403, and unknown venues 404.

`GET /venues/{venue_id}/seats` is public. It returns an array ordered
lexicographically by section and row, then numerically by seat number. `limit`
defaults to 100 (range 1–500); `offset` defaults to 0 and must be nonnegative.
Existing venues without seats return an empty array; unknown venues return 404.
Venues with seats cannot be deleted while those seats reference them.

Seats describe physical locations, not event availability. Editing, deletion,
layout generation, prices, and booking remain deferred. The concurrency test uses
independent committed transactions and cleans up its own seats, venue, and user.
