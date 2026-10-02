# VenuePass API

Event ticket reservation API built with Python 3.14, FastAPI, async SQLAlchemy,
PostgreSQL, Alembic, and uv. Supports health checking, event creation, and event
browsing, plus local user registration and JWT authentication. Authenticated users can reserve event seats with safe retries and double-booking protection.

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
with `uv run alembic upgrade head`; `current` reports `20261002_0009` as the head.
Revision `20260928_0007` adds reservations and unique seat claims while preserving
existing records. Downgrading it removes bookings, including successful retry keys.
Revision `20260928_0006` links events to venues and adds organizer grants and fixed
seat membership. It refuses to upgrade if legacy events exist: those records need
an explicit venue mapping, rather than a guessed match or deletion. Downgrade
restores event venue text from venue names and removes grants and seat membership.
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
fields, the authenticated user’s `organizer_id`, and database-generated `created_at`.
`name` is trimmed and must contain 1–255 characters. Supply `venue_id` as a UUID.
Capacity is calculated from all venue seats when the event is created. Supplying
legacy `venue` or `capacity` input fields returns HTTP 422.
`starts_at` must include a timezone and be in the future. Optional `ends_at` must
include a timezone and be strictly later than `starts_at`; omitted or null means
no fixed finish time. `description` is optional. Invalid input returns HTTP 422.

Choose future dates when trying this example:

```bash
curl -X POST http://127.0.0.1:8000/events \
  -H 'Authorization: Bearer REPLACE_WITH_ORGANIZER_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"name":"Python meetup","venue_id":"REPLACE_WITH_VENUE_UUID","description":"An evening of talks","starts_at":"2099-10-01T18:00:00Z","ends_at":"2099-10-01T20:00:00Z"}'

curl 'http://127.0.0.1:8000/events?limit=20&offset=0'
curl 'http://127.0.0.1:8000/events/REPLACE_WITH_EVENT_UUID'
```

`GET /events` returns an array ordered by start time, then UUID, including past
events but excluding cancelled events by default. `limit` defaults to 20 (range 1–100); `offset` defaults to 0 and must be
nonnegative. `GET /events/{id}` returns one event, HTTP 404 for an unknown UUID,
or HTTP 422 for a malformed UUID. Responses always include nullable `ends_at` and `cancelled_at`.
Event listing accepts optional filters, combined with AND before pagination:

| Parameter | Behavior |
| --- | --- |
| `q` | Case-insensitive literal substring of the name; trimmed, 1–255 characters |
| `venue_id` | Only events at the supplied venue UUID |
| `starts_from` | Inclusive start-time lower bound, with a timezone |
| `starts_before` | Exclusive start-time upper bound, with a timezone |
| `include_cancelled` | Include cancelled events; defaults to false |
| `upcoming_only` | When true, starts strictly after current UTC time; defaults to false |

Search treats `%` and `_` as literal characters, not wildcards. Invalid filters,
including empty search text or `starts_from >= starts_before`, return HTTP 422.
No matches (including an unknown venue UUID) return an empty array. Supplying no
filters preserves the existing listing behavior; all responses use the same fields.

```bash
curl --get 'http://127.0.0.1:8000/events' \
  --data-urlencode 'q=python' \
  --data-urlencode 'upcoming_only=true'
curl --get 'http://127.0.0.1:8000/events' \
  --data-urlencode 'venue_id=REPLACE_WITH_VENUE_UUID' \
  --data-urlencode 'starts_from=2099-10-01T00:00:00Z' \
  --data-urlencode 'starts_before=2099-11-01T00:00:00Z'
```

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

Venue editing, deletion, and shared staff access remain deferred. Events reference
venues through `venue_id`; seats referenced by event membership are protected from deletion.

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
layout generation and prices remain deferred. The concurrency test uses
independent committed transactions and cleans up its own seats, venue, and user.

## Venue authorization and event seat membership

A venue owner with organizer permission can create events at their own venue.
Other organizers need an explicit grant. Owners with current venue-manager
permission manage these grants:

- `PUT /venues/{venue_id}/organizers/{organizer_id}` grants ongoing access. Repeated
  grants return HTTP 204. The target must be an existing organizer (409 otherwise).
- `DELETE /venues/{venue_id}/organizers/{organizer_id}` revokes future creation
  access, returning 204 even when no grant exists. It does not remove old events.
- `GET /venues/{venue_id}/organizers` returns an array of explicit organizer UUIDs,
  ordered by UUID, with `limit` default 20 (1–100) and nonnegative `offset` default 0.

These endpoints return 401 without valid authentication, 403 for missing manager
permission or ownership, and 404 for missing resources. Owners have implicit event
creation access but are not automatically included in the explicit grant list.
Current organizer permission is always required, even with an existing grant.

Event creation rejects unauthorized venue use with 403, missing venues with 404,
and venues without seats with 409. It stores the event, capacity, and all current
venue seat memberships in one transaction. Grants, revocations, seat additions,
and event creation acquire the same venue row lock, so simultaneous changes take
effect in lock-acquisition order. Creating an event does not reserve a time slot;
scheduling conflicts are not checked, and `ends_at` remains optional.

`GET /events/{event_id}/seats` publicly lists the event's fixed seat membership,
using the same fields and section/row/number ordering as venue seats. `limit`
defaults to 100 (1–500), with nonnegative `offset` defaulting to 0. Missing events
return 404. Adding venue seats later does not change existing event membership or
capacity. Each event seat now includes `is_available`, true only when unbooked
and the event has not started. It never exposes who booked the seat. Availability
is informational: booking creation checks it again inside its transaction.

Typical workflow: create a venue, add seats, grant organizer access when needed,
then create an event with that venue's UUID. Seat subsets and pricing remain deferred.

## Confirmed reservations

Any authenticated user can book seats; organizer or venue-manager permission is
not required. Multiple reservations per user and event are allowed. Every booking
is confirmed immediately, without payment or expiry.

```bash
curl -X POST http://127.0.0.1:8000/events/EVENT_UUID/reservations \
  -H 'Authorization: Bearer USER_TOKEN' \
  -H 'Idempotency-Key: unique-booking-request-1' \
  -H 'Content-Type: application/json' \
  -d '{"seat_ids":["SEAT_UUID_1","SEAT_UUID_2"]}'
curl -H 'Authorization: Bearer USER_TOKEN' \
  'http://127.0.0.1:8000/users/me/reservations?limit=20&offset=0'
curl -H 'Authorization: Bearer USER_TOKEN' \
  'http://127.0.0.1:8000/reservations/RESERVATION_UUID'
```

Creation accepts 1–20 distinct UUIDs and requires an `Idempotency-Key` containing
1–128 printable, non-whitespace ASCII characters. Response HTTP 201 contains `id`,
`event_id`, `user_id`, `seat_ids` sorted by UUID, `created_at`, nullable `cancelled_at`, and nullable `cancellation_reason`. The key is never
returned. Ownership comes from authentication, not submitted input.

Keys are scoped to the user and event and retained for the booking's lifetime.
Retry the same key and seat set (in any order) to recover the original HTTP 201
response, even after the event starts. A different seat set with the same successful
key returns 409. Failed requests do not consume their keys. Use a new key for each
new intended booking, and retain the original key when retrying an uncertain result.

The service locks the event row, checks successful retries, then checks the current
UTC time, fixed seat membership, and existing bookings. At or after the start time,
new bookings return 409. Booked seats also return 409; seats outside the event and
invalid inputs return 422; missing events return 404; unauthenticated requests
return 401. The entire booking, seat claims, and retry identity commit together.
A partial unique database index also prevents duplicate active event-seat claims.

Reservation retrieval is owner-only: other users' reservation IDs return the same
404 as missing IDs. History returns only the current user's bookings, newest first,
with descending UUID as a tie-breaker. `limit` defaults to 20 (1–100); `offset`
defaults to 0 and must be nonnegative. No organizer endpoint exposes customer bookings.

`POST /reservations/{reservation_id}/cancel` cancels the entire reservation and
returns HTTP 200 with `cancelled_at` set. It requires authentication but no request
body or idempotency key. Only the reservation owner can cancel it; other users and
missing reservations receive 404. Active reservations can be cancelled strictly
before the event starts; at or after that time cancellation returns 409. Repeated
cancellation returns the same timestamp, even after the event starts.

Cancellation retains the reservation, original key, and seat history. All its seat
claims receive `released_at` in the same transaction and become available again.
Retrying the original creation request still returns HTTP 201 with the original
reservation's current cancelled state; it never books the seats again. Use a new
key for a new booking. Booking and cancellation acquire the same event row lock,
so concurrent requests take effect in lock-acquisition order. Downgrading the
cancellation migration refuses to discard existing cancellation history.

Payment, temporary holds, expiry, and editing remain deferred. Tests
cover simultaneous seat conflicts and same-key requests using independent database
transactions and observed lock waits; their committed test records are cleaned up.

## Organizer event cancellation

`POST /events/{event_id}/cancel` requires a bearer token and current organizer
permission. Only the owning organizer can cancel, even if their venue access has
been revoked. Venue owners and other authorized organizers cannot cancel someone
else's event. No body or idempotency key is required.

```bash
curl -X POST http://127.0.0.1:8000/events/EVENT_UUID/cancel \
  -H 'Authorization: Bearer ORGANIZER_TOKEN'
curl 'http://127.0.0.1:8000/events?include_cancelled=true'
```

First cancellation is allowed strictly before the event starts. It returns HTTP
200 with the event's UTC `cancelled_at`. Repeated cancellation preserves that
value and succeeds even after the start. Missing events return 404, insufficient
permission or ownership returns 403, unauthenticated requests return 401, and
first cancellation at or after the start returns 409.

The transaction cancels active reservations with `cancellation_reason` set to
`event_cancelled` and releases their active seat claims using the same timestamp.
Earlier customer cancellations keep their timestamps and `customer` reasons.
Active bookings have null cancellation timestamps and reasons. Events, bookings,
seat history, and original idempotency keys are retained.

New bookings for cancelled events return 409. Matching original creation retries
still return HTTP 201 with the original booking's current cancelled state. All
seats of a cancelled event report unavailable. Public listings hide cancelled
events by default; `include_cancelled=true` includes them under the other filters.
Direct event lookup remains available; `upcoming_only` still refers to start time.

Booking creation, customer cancellation, and event cancellation lock the same
row in `events`, so concurrent operations take effect in lock-acquisition order.
Migration downgrade refuses to erase event cancellation history. Restoration,
notifications, and refunds are not implemented.

## Organizer event management

`GET /users/me/events` requires authentication and current organizer permission.
It lists only the caller's events, including past and cancelled events, ordered
newest-created first with descending UUID as a tie-breaker. `limit` defaults to 20
(1–100), and `offset` defaults to 0 (nonnegative). An empty list returns HTTP 200.

`PATCH /events/{event_id}` updates the owning organizer's event name and/or
description and returns HTTP 200 with the complete event response. The name is
trimmed and must contain 1–255 characters; it cannot be null. Omitted fields stay
unchanged, while `description: null` clears the description. At least one field
must be supplied. Unknown fields, including dates, venue, capacity, ownership,
and cancellation state, return HTTP 422. Descriptions retain creation's string
behavior and may be empty.

```bash
curl 'http://127.0.0.1:8000/users/me/events?limit=20&offset=0' \
  -H 'Authorization: Bearer ORGANIZER_TOKEN'
curl -X PATCH http://127.0.0.1:8000/events/EVENT_UUID \
  -H 'Authorization: Bearer ORGANIZER_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"name":"Updated event name","description":null}'
```

Editing requires current organizer permission and event ownership, but not current
venue access. Unauthenticated requests return 401, missing permission or ownership
returns 403, and missing events return 404. Cancelled events and events at or after
their start time reject editing with HTTP 409. Editing locks the same event row as
cancellation and checks event state and the clock after any lock wait. It changes
neither the schedule nor seats or reservations, and commits supplied fields together.

## Customer demo

Start the API as usual and open **http://127.0.0.1:8000/demo/**. The demo is served
by FastAPI using HTML, CSS, and JavaScript; no frontend build or separate service
is required. Swagger remains at `/docs`. All browser requests use the same origin.

The public demo supports event-name, venue, start-date, and upcoming-only filters,
event pagination, and seat availability grouped by section and row. Dates are
shown in your browser's timezone; local date filters are converted to absolute
instants before being submitted. Seat groups represent labels, not a floor plan.
It fetches every seat page rather than omitting larger layouts.

The demo reads existing API data and does not seed or reset the database. If there
are no events, use Swagger and the documented organizer workflow to create a
venue, seats, and a future event. Payments and seat holds are outside the demo.

Browser checks use Playwright as development tooling. Install Chromium once:

```bash
uv run playwright install chromium
VENUEPASS_BROWSER_TESTS=1 uv run pytest tests/test_demo_browser.py
```

Ordinary `uv run pytest` skips browser checks unless enabled. Browser tests serve
the actual frontend and mock API responses, so they do not modify a database;
the PostgreSQL integration suite verifies backend behavior separately.

The customer demo supports account registration, login, logout, and displaying the
current account. Registration leads to an explicit login. Access tokens live only
in JavaScript memory, never browser storage; refreshing logs you out. Logout
clears the local session but does not revoke an already-issued server token.
Expired authenticated requests prompt for login again. Passwords are cleared from
the form after submission and are not included in URLs or logs.
