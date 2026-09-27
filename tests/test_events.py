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


async def test_create_and_retrieve(event_client, payload):
    """Creation returns generated fields and writes data visible to later requests."""
    client, connection = event_client
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
    fetched = await client.get(f"/events/{event['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == event


@pytest.mark.parametrize("explicit_null", [False, True])
async def test_optional_end(event_client, payload, explicit_null):
    """An unspecified finish is represented consistently as null."""
    client, _ = event_client
    payload.pop("ends_at")
    payload.pop("description")
    if explicit_null:
        payload["ends_at"] = None
    response = await client.post("/events", json=payload)
    assert response.status_code == 201
    assert response.json()["ends_at"] is None
    assert response.json()["description"] is None


async def test_listing_order_pagination_and_past_events(event_client):
    """Sort by start then UUID, and continue exposing events after they start."""
    client, connection = event_client
    assert (await client.get("/events")).json() == []
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = past + timedelta(days=3)
    ids = [UUID(int=3), UUID(int=2), UUID(int=1)]
    # Seed past data directly because the creation API deliberately rejects it.
    await connection.execute(Event.__table__.insert(), [
        {"id": id_, "name": "Event", "venue": "Hall", "capacity": 10,
         "starts_at": start}
        for id_, start in zip(ids, [future, past, past])
    ])
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
async def test_invalid_input(event_client, payload, field, value):
    """Invalid fields fail validation without writing an event."""
    client, _ = event_client
    payload[field] = value
    assert (await client.post("/events", json=payload)).status_code == 422
    assert (await client.get("/events")).json() == []


@pytest.mark.parametrize("hours", [0, -1])
async def test_end_must_follow_start(event_client, payload, hours):
    """Equal and earlier end times are rejected."""
    client, _ = event_client
    payload["ends_at"] = (
        datetime.fromisoformat(payload["starts_at"]) + timedelta(hours=hours)
    ).isoformat()
    assert (await client.post("/events", json=payload)).status_code == 422


async def test_missing_and_malformed_ids(event_client):
    """Distinguish a missing event from an invalid identifier."""
    client, _ = event_client
    assert (await client.get(f"/events/{uuid4()}")).status_code == 404
    assert (await client.get("/events/not-a-uuid")).status_code == 422


@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_invalid_pagination(event_client, query):
    """Keep page sizes bounded and offsets nonnegative."""
    client, _ = event_client
    assert (await client.get("/events", params=query)).status_code == 422
