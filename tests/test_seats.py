"""Exercise atomic seat batches, venue ownership, and concurrent conflicts."""

import asyncio
from uuid import uuid4
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import Settings
from app.main import create_app
from app.models import Seat, User, Venue
from app.security import create_access_token

pytestmark = pytest.mark.integration
SECRET = 'test-signing-secret-that-is-at-least-32-characters'


def seat(number, section='Main', row='A'):
    """Build a physical seat input with overridable labels."""
    return {'section': section, 'row': row, 'number': number}


@pytest.fixture
async def seating(event_client):
    """Seed an owner and venue inside the shared rollback transaction."""
    client, connection = event_client
    owner, venue = uuid4(), uuid4()
    await connection.execute(User.__table__.insert().values(id=owner, email=f'{owner}@example.com', password_hash='unused', is_venue_manager=True))
    await connection.execute(Venue.__table__.insert().values(id=venue, owner_id=owner, name='Hall', address='Street'))
    client.headers['Authorization'] = 'Bearer ' + create_access_token(owner, SECRET)
    return client, connection, owner, venue


async def test_batch_order_and_public_listing(seating):
    """Creation preserves request order; browsing uses labels and numeric numbering."""
    client, _, _, venue = seating
    path = f'/venues/{venue}/seats'
    assert (await client.get(path)).json() == []
    response = await client.post(path, json={'seats': [seat(10, ' Main '), seat(2), seat(1, row='B')]})
    assert response.status_code == 201
    rows = response.json()
    assert [item['number'] for item in rows] == [10, 2, 1]
    assert all(item['section'] == 'Main' and item['venue_id'] == str(venue) for item in rows)
    assert len({item['id'] for item in rows}) == 3
    client.headers.clear()
    assert [item['number'] for item in (await client.get(path)).json()] == [2, 10, 1]
    assert (await client.get(path+'?limit=1&offset=1')).json()[0]['number'] == 10
    assert (await client.get(path+'?offset=3')).json() == []
    for query in ['limit=0', 'limit=501', 'offset=-1']:
        assert (await client.get(path+'?'+query)).status_code == 422
    assert (await client.get(f'/venues/{uuid4()}/seats')).status_code == 404
    assert (await client.get('/venues/invalid/seats')).status_code == 422


async def test_conflicts_roll_back_entire_batch(seating):
    """Normalized duplicates and existing seats reject all additions in the batch."""
    client, _, _, venue = seating
    path = f'/venues/{venue}/seats'
    assert (await client.post(path, json={'seats': [seat(2), seat(2, ' Main ')]})).status_code == 409
    assert (await client.get(path)).json() == []
    assert (await client.post(path, json={'seats': [seat(2)]})).status_code == 201
    assert (await client.post(path, json={'seats': [seat(1), seat(2), seat(3)]})).status_code == 409
    assert [item['number'] for item in (await client.get(path)).json()] == [2]
    # Labels are case-sensitive, so a different section is a distinct identity.
    assert (await client.post(path, json={'seats': [seat(2, 'main')]})).status_code == 201


async def test_permissions(seating):
    """Both current venue-manager permission and ownership are required."""
    client, connection, owner, venue = seating
    path = f'/venues/{venue}/seats'
    body = {'seats': [seat(1)]}
    assert (await client.post(f'/venues/{uuid4()}/seats', json=body)).status_code == 404
    client.headers.clear()
    assert (await client.post(path, json=body)).status_code == 401
    client.headers['Authorization'] = 'Bearer ' + create_access_token(owner, SECRET)
    await connection.execute(update(User).where(User.id == owner).values(is_venue_manager=False))
    assert (await client.post(path, json=body)).status_code == 403
    other = uuid4()
    await connection.execute(User.__table__.insert().values(id=other, email=f'{other}@example.com', password_hash='unused', is_venue_manager=True))
    client.headers['Authorization'] = 'Bearer ' + create_access_token(other, SECRET)
    assert (await client.post(path, json=body)).status_code == 403
    assert (await client.get(path)).json() == []


@pytest.mark.parametrize('value', [seat(0), seat(-1), seat(True), seat(1.5), seat(2147483648), seat(1, ''), seat(1, row=' '), seat(1, 'x'*101), seat(1, row='x'*101)])
async def test_invalid_seats(seating, value):
    """Invalid labels and numbers fail before any seat is stored."""
    client, _, _, venue = seating
    assert (await client.post(f'/venues/{venue}/seats', json={'seats': [value]})).status_code == 422


async def test_batch_limits(seating):
    """Accept the maximum batch size and reject empty or oversized requests."""
    client, _, _, venue = seating
    path = f'/venues/{venue}/seats'
    for count in (0, 501):
        assert (await client.post(path, json={'seats': [seat(n+1) for n in range(count)]})).status_code == 422
    response = await client.post(path, json={'seats': [seat(n+1) for n in range(500)]})
    assert response.status_code == 201 and len(response.json()) == 500
    assert len((await client.get(path)).json()) == 100


async def test_competing_batches():
    """Real independent transactions allow one whole batch and roll back the loser."""
    settings = Settings()
    url = str(settings.test_database_url)
    assert make_url(url).database == 'venuepass_db_test'
    engine = create_async_engine(url)
    owner, venue = uuid4(), uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(User.__table__.insert().values(id=owner, email=f'{owner}@example.com', password_hash='unused', is_venue_manager=True))
            await connection.execute(Venue.__table__.insert().values(id=venue, owner_id=owner, name='Race', address='Street'))
        app = create_app(Settings(database_url=url, jwt_secret=SECRET, _env_file=None))
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', headers={'Authorization': 'Bearer '+create_access_token(owner, SECRET)}) as client:
                responses = await asyncio.gather(*[
                    client.post(f'/venues/{venue}/seats', json={'seats': batch})
                    for batch in ([seat(3), seat(1)], [seat(3), seat(2)])
                ])
                assert sorted(response.status_code for response in responses) == [201, 409]
                winner = next(response.json() for response in responses if response.status_code == 201)
        async with engine.connect() as connection:
            actual = set((await connection.execute(select(Seat.number).where(Seat.venue_id == venue))).scalars())
            assert actual == {item['number'] for item in winner}
    finally:
        # Real commits are necessary here; cleanup removes only this test's records.
        async with engine.begin() as connection:
            await connection.execute(delete(Seat).where(Seat.venue_id == venue))
            await connection.execute(delete(Venue).where(Venue.id == venue))
            await connection.execute(delete(User).where(User.id == owner))
        await engine.dispose()
