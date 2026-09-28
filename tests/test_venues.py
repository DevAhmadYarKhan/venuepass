"""Verify venue permissions, ownership, input validation, and public browsing."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, Venue
from app.security import create_access_token
from app.services.users import promote_venue_manager

pytestmark = pytest.mark.integration
SECRET = 'test-signing-secret-that-is-at-least-32-characters'


@pytest.fixture
async def manager(event_client):
    """Seed a venue-only manager in the rollback transaction and issue a token."""
    client, connection = event_client
    owner = uuid4()
    await connection.execute(User.__table__.insert().values(
        id=owner, email=f'{owner}@example.com', password_hash='unused', is_venue_manager=True,
    ))
    client.headers['Authorization'] = 'Bearer ' + create_access_token(owner, SECRET)
    return client, connection, owner


async def test_permissions_and_promotion(event_client):
    """Self-promotion is ignored; organizer status alone does not allow venues."""
    client, connection = event_client
    payload = {'name': 'Hall', 'address': '1 Main Street'}
    assert (await client.post('/venues', json=payload)).status_code == 401
    credentials = {'email': 'manager@example.com', 'password': 'a long enough password'}
    registered = await client.post('/auth/register', json={
        **credentials, 'is_organizer': True, 'is_venue_manager': True,
    })
    assert registered.status_code == 201
    account = registered.json()
    assert account['is_organizer'] is account['is_venue_manager'] is False
    login = await client.post('/auth/login', json=credentials)
    client.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
    assert (await client.post('/venues', json=payload)).status_code == 403
    await connection.execute(User.__table__.update().where(User.id == UUID(account['id'])).values(is_organizer=True))
    assert (await client.post('/venues', json=payload)).status_code == 403
    async with AsyncSession(bind=connection, join_transaction_mode='create_savepoint') as session:
        await promote_venue_manager(session, account['email'])
        await promote_venue_manager(session, account['email'])
    # A previously issued token sees the new permission and preserves organizer access.
    me = (await client.get('/users/me')).json()
    assert me['is_organizer'] is me['is_venue_manager'] is True
    assert (await client.post('/venues', json=payload)).status_code == 201


async def test_creation_ownership_and_public_detail(manager):
    """Persist the authenticated owner, trim details, and expose public retrieval."""
    client, connection, owner = manager
    response = await client.post('/venues', json={
        'name': ' Hall ', 'address': ' 1 Main Street ', 'owner_id': str(uuid4()),
    })
    assert response.status_code == 201
    venue = response.json()
    assert venue['name'] == 'Hall' and venue['address'] == '1 Main Street'
    assert venue['owner_id'] == str(owner)
    assert datetime.fromisoformat(venue['created_at']).tzinfo is not None
    assert await connection.scalar(select(Venue.owner_id).where(Venue.id == UUID(venue['id']))) == owner
    # Venue permission must not grant event-creation permission.
    event = {'name': 'Talk', 'venue': 'Hall', 'capacity': 10,
             'starts_at': (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}
    assert (await client.post('/events', json=event)).status_code == 403
    # Duplicate names represent distinct venues and are allowed.
    assert (await client.post('/venues', json={'name': 'Hall', 'address': '2 Main Street'})).status_code == 201
    client.headers.clear()
    assert (await client.get('/venues/' + venue['id'])).json() == venue
    assert (await client.get('/venues/' + str(uuid4()))).status_code == 404
    assert (await client.get('/venues/invalid')).status_code == 422
    # PostgreSQL protects owners even when deletion bypasses the application.
    async with connection.begin_nested():
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(delete(User).where(User.id == owner))


async def test_public_listing_order_and_pagination(manager):
    """Creation time orders pages; UUID breaks ties deterministically."""
    client, connection, owner = manager
    client.headers.clear()
    assert (await client.get('/venues')).json() == []
    now = datetime.now(timezone.utc)
    await connection.execute(Venue.__table__.insert(), [
        {'id': UUID(int=n), 'owner_id': owner, 'name': 'Hall', 'address': 'Street', 'created_at': when}
        for n, when in [(3, now + timedelta(seconds=1)), (2, now), (1, now)]
    ])
    assert [item['id'] for item in (await client.get('/venues')).json()] == [str(UUID(int=n)) for n in (1, 2, 3)]
    assert [item['id'] for item in (await client.get('/venues?limit=1&offset=1')).json()] == [str(UUID(int=2))]
    assert (await client.get('/venues?offset=3')).json() == []
    for query in ['limit=0', 'limit=101', 'offset=-1']:
        assert (await client.get('/venues?' + query)).status_code == 422


@pytest.mark.parametrize('field,value', [('name', ' '), ('address', '\t'), ('name', 'x'*256), ('address', 'x'*1001), ('name', None), ('address', None)])
async def test_invalid_details(manager, field, value):
    """Reject blank, missing-value, or overlong venue details before insertion."""
    client, _, _ = manager
    payload = {'name': 'Hall', 'address': 'Street', field: value}
    assert (await client.post('/venues', json=payload)).status_code == 422
    assert (await client.get('/venues')).json() == []
