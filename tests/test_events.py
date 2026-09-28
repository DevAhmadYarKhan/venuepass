"""Exercise event endpoints against PostgreSQL with per-test transaction isolation."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models import Event

pytestmark = pytest.mark.integration


@pytest.fixture
def payload():
    """Use relative future times so tests do not expire as the calendar advances."""
    start = datetime.now(timezone.utc) + timedelta(days=7)
    return {
        "name": "  Python meetup  ", "venue": "  Main hall  ",
        "description": "An evening of talks", "capacity": 50,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=2)).isoformat(),
    }


async def test_create_and_retrieve(organizer_client, payload):
    """Creation returns generated fields and writes data visible to later requests."""
    client, connection, organizer_id = organizer_client
    response = await client.post("/events", json=payload)
    assert response.status_code == 201
    event = response.json()
    assert event["name"] == "Python meetup"
    assert event["venue"] == "Main hall"
    assert event["description"] == payload["description"]
    assert event["capacity"] == 50
    assert datetime.fromisoformat(event["created_at"]).tzinfo is not None
    assert datetime.fromisoformat(event["ends_at"]) == datetime.fromisoformat(payload["ends_at"])
    assert await connection.scalar(select(Event.id).where(Event.id == UUID(event["id"]))) == UUID(event["id"])
    assert event["organizer_id"] == str(organizer_id)
    client.headers.clear()  # Event retrieval remains public.
    fetched = await client.get(f"/events/{event['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == event


@pytest.mark.parametrize("explicit_null", [False, True])
async def test_optional_end(organizer_client, payload, explicit_null):
    """An unspecified finish is represented consistently as null."""
    client, _, _ = organizer_client
    payload.pop("ends_at")
    payload.pop("description")
    if explicit_null:
        payload["ends_at"] = None
    response = await client.post("/events", json=payload)
    assert response.status_code == 201
    assert response.json()["ends_at"] is None
    assert response.json()["description"] is None


async def test_listing_order_pagination_and_past_events(organizer_client):
    """Sort by start then UUID, and continue exposing events after they start."""
    client, connection, organizer_id = organizer_client
    assert (await client.get("/events")).json() == []
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = past + timedelta(days=3)
    ids = [UUID(int=3), UUID(int=2), UUID(int=1)]
    # Seed past data directly because the creation API deliberately rejects it.
    await connection.execute(Event.__table__.insert(), [
        {"id": id_, "name": "Event", "venue": "Hall", "capacity": 10,
         "starts_at": start, "organizer_id": organizer_id}
        for id_, start in zip(ids, [future, past, past])
    ])
    client.headers.clear()  # Listing remains public, including seeded past events.
    response = await client.get("/events")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [str(UUID(int=n)) for n in (1, 2, 3)]
    page = await client.get("/events", params={"limit": 1, "offset": 1})
    assert [row["id"] for row in page.json()] == [str(UUID(int=2))]
    assert (await client.get("/events", params={"offset": 3})).json() == []
    assert (await client.get(f"/events/{UUID(int=1)}")).status_code == 200


@pytest.mark.parametrize("field,value", [
    ("name", " "), ("venue", "\t"), ("name", "x" * 256),
    ("venue", "x" * 256), ("capacity", 0), ("capacity", -1),
    ("capacity", 1.5), ("capacity", True), ("capacity", 2_147_483_648),
    ("starts_at", "2000-01-01T00:00:00Z"),
    ("starts_at", "2099-01-01T00:00:00"),
    ("ends_at", "2099-01-01T00:00:00"),
])
async def test_invalid_input(organizer_client, payload, field, value):
    """Invalid fields fail validation without writing an event."""
    client, _, _ = organizer_client
    payload[field] = value
    assert (await client.post("/events", json=payload)).status_code == 422
    assert (await client.get("/events")).json() == []


@pytest.mark.parametrize("hours", [0, -1])
async def test_end_must_follow_start(organizer_client, payload, hours):
    """Equal and earlier end times are rejected."""
    client, _, _ = organizer_client
    payload["ends_at"] = (
        datetime.fromisoformat(payload["starts_at"]) + timedelta(hours=hours)
    ).isoformat()
    assert (await client.post("/events", json=payload)).status_code == 422


async def test_missing_and_malformed_ids(organizer_client):
    """Distinguish a missing event from an invalid identifier."""
    client, _, _ = organizer_client
    assert (await client.get(f"/events/{uuid4()}")).status_code == 404
    assert (await client.get("/events/not-a-uuid")).status_code == 422


@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_invalid_pagination(organizer_client, query):
    """Keep page sizes bounded and offsets nonnegative."""
    client, _, _ = organizer_client
    assert (await client.get("/events", params=query)).status_code == 422


async def test_permissions_promotion_and_spoofing(event_client, payload):
    """Permission comes from current account data and ownership ignores client input."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.users import promote_organizer

    client, connection = event_client
    response = await client.post('/events', json=payload)
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'
    account = {'email': 'organizer@example.com', 'password': 'a sufficiently long password'}
    registered = await client.post('/auth/register', json={**account, 'is_organizer': True})
    assert registered.status_code == 201
    assert registered.json()['is_organizer'] is False
    login = await client.post('/auth/login', json=account)
    client.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
    assert (await client.post('/events', json=payload)).status_code == 403
    async with AsyncSession(bind=connection, join_transaction_mode='create_savepoint') as session:
        await promote_organizer(session, account['email'])
        await promote_organizer(session, account['email'])  # Idempotent promotion.
    assert (await client.get('/users/me')).json()['is_organizer'] is True
    # Reuse the pre-promotion JWT: permission is read from PostgreSQL per request.
    created = await client.post('/events', json={**payload, 'organizer_id': str(uuid4())})
    assert created.status_code == 201
    owner = UUID(registered.json()['id'])
    assert created.json()['organizer_id'] == str(owner)
    assert await connection.scalar(select(Event.organizer_id).where(Event.id == UUID(created.json()['id']))) == owner
