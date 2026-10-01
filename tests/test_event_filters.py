"""Verify public discovery filters against actual PostgreSQL matching semantics."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.models import Event, Venue
from app.services import events

pytestmark = pytest.mark.integration


@pytest.fixture
async def discovery(organizer_client):
    """Seed two venues and events around a fixed clock, including literal wildcards."""
    client, connection, owner = organizer_client
    venues = [uuid4(), uuid4()]
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    await connection.execute(Venue.__table__.insert(), [
        {'id': venue, 'owner_id': owner, 'name': 'Hall', 'address': 'Street'} for venue in venues
    ])
    rows = [
        (1, 'Python past', -1, 0), (2, 'Python now', 0, 0),
        (3, 'PYTHON meetup', 1, 0), (4, 'Python elsewhere', 1, 1),
        (5, 'Music 100%_live/show', 2, 0), (6, 'Music 100xxlive/show', 3, 0),
    ]
    await connection.execute(Event.__table__.insert(), [
        {'id': UUID(int=id_), 'name': name, 'starts_at': now + timedelta(days=days),
         'venue_id': venues[venue], 'organizer_id': owner, 'capacity': 1}
        for id_, name, days, venue in rows
    ])
    client.headers.clear()  # Discovery must remain accessible without authentication.
    return client, venues, now


async def matches(client, params):
    """Return ordered IDs while checking that valid filter requests succeed."""
    response = await client.get('/events', params=params)
    assert response.status_code == 200, response.text
    return [UUID(row['id']).int for row in response.json()]


async def test_individual_filters_and_literal_search(discovery, monkeypatch):
    """Name matching ignores case but respects %, _, and the escape character literally."""
    client, venues, now = discovery
    monkeypatch.setattr(events, 'utc_now', lambda: now)
    assert await matches(client, {}) == [1, 2, 3, 4, 5, 6]
    assert await matches(client, {'q': '  pYtHoN  '}) == [1, 2, 3, 4]
    assert await matches(client, {'q': '%_'}) == [5]
    assert await matches(client, {'q': '_live/'}) == [5]
    assert await matches(client, {'q': "' OR 1=1 --"}) == []
    assert await matches(client, {'q': 'missing'}) == []
    assert await matches(client, {'venue_id': str(venues[1])}) == [4]
    assert await matches(client, {'venue_id': str(uuid4())}) == []
    assert await matches(client, {'upcoming_only': 'true'}) == [3, 4, 5, 6]
    assert await matches(client, {'upcoming_only': 'false'}) == [1, 2, 3, 4, 5, 6]


async def test_date_boundaries_and_timezone_equivalence(discovery):
    """Lower bounds are inclusive and upper bounds exclusive, comparing absolute instants."""
    client, venues, now = discovery
    tomorrow = now + timedelta(days=1)
    assert await matches(client, {'starts_from': now.isoformat()}) == [2, 3, 4, 5, 6]
    assert await matches(client, {'starts_before': tomorrow.isoformat()}) == [1, 2]
    params = {'starts_from': now.isoformat(), 'starts_before': tomorrow.isoformat()}
    assert await matches(client, params) == [2]
    offset = timezone(timedelta(hours=5, minutes=30))
    shifted = {key: datetime.fromisoformat(value).astimezone(offset).isoformat() for key, value in params.items()}
    assert await matches(client, shifted) == [2]


async def test_combined_filters_and_pagination(discovery, monkeypatch):
    """Combine conditions before offset/limit, retaining UUID ordering for tied starts."""
    client, venues, now = discovery
    monkeypatch.setattr(events, 'utc_now', lambda: now)
    params = {'q': 'python', 'upcoming_only': 'true', 'starts_from': now.isoformat(),
              'starts_before': (now + timedelta(days=2)).isoformat()}
    assert await matches(client, params) == [3, 4]
    assert await matches(client, {**params, 'limit': 1, 'offset': 1}) == [4]
    assert await matches(client, {**params, 'venue_id': str(venues[0])}) == [3]
    assert await matches(client, {**params, 'offset': 2}) == []
    assert await matches(client, {**params, 'starts_from': (now + timedelta(hours=1)).isoformat()}) == [3, 4]


@pytest.mark.parametrize('params', [
    {'q': ''}, {'q': ' \t '}, {'q': 'x' * 256}, {'venue_id': 'invalid'},
    {'starts_from': '2030-01-01T00:00:00'}, {'starts_before': '2030-01-01'},
    {'starts_from': 'invalid'}, {'upcoming_only': 'perhaps'},
    {'starts_from': '2030-01-01T00:00:00Z', 'starts_before': '2030-01-01T00:00:00Z'},
    {'starts_from': '2030-01-02T00:00:00Z', 'starts_before': '2030-01-01T00:00:00Z'},
])
async def test_invalid_filters(discovery, params):
    """Malformed input and empty/reversed intervals fail as HTTP validation errors."""
    client, venues, now = discovery
    assert (await client.get('/events', params=params)).status_code == 422
