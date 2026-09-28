"""Verify venue authorization, frozen membership, and serialized event creation."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.engine import make_url
from app.config import Settings
from app.models import Event, EventSeat, Seat, User, Venue, VenueOrganizer
from app.security import create_access_token
from app.schemas.events import EventCreate
from app.services import events
from app.errors import VenueAccessDenied

pytestmark = pytest.mark.integration
SECRET = 'test-signing-secret-that-is-at-least-32-characters'


def body(venue):
    """Use a relative future date and omit server-derived capacity."""
    return {'name': 'Concert', 'venue_id': str(venue), 'starts_at': (datetime.now(timezone.utc)+timedelta(days=5)).isoformat()}


def authorize(client, user):
    """Switch request identity without replacing authentication dependencies."""
    client.headers['Authorization'] = 'Bearer '+create_access_token(user, SECRET)


@pytest.fixture
async def setup(event_client):
    """Seed an owner, guest organizer, and two physical seats."""
    client, connection = event_client
    owner, guest, venue = uuid4(), uuid4(), uuid4()
    for user in (owner, guest):
        await connection.execute(User.__table__.insert().values(id=user, email=f'{user}@example.com', password_hash='unused', is_organizer=True, is_venue_manager=True))
    await connection.execute(Venue.__table__.insert().values(id=venue, owner_id=owner, name='Hall', address='Street'))
    await connection.execute(Seat.__table__.insert(), [{'venue_id': venue, 'section': 'Main', 'row': 'A', 'number': n} for n in (1,2)])
    return client, connection, owner, guest, venue


async def test_grants_and_fixed_membership(setup):
    """Owners grant ongoing access; revocation affects only future events."""
    client, connection, owner, guest, venue = setup
    path=f'/venues/{venue}/organizers'
    assert (await client.get(path)).status_code == 401
    authorize(client,guest)
    assert (await client.post('/events',json=body(venue))).status_code == 403
    assert (await client.put(f'{path}/{guest}')).status_code == 403
    assert (await client.get(path)).status_code == 403
    authorize(client,owner)
    for _ in range(2):
        assert (await client.put(f'{path}/{guest}')).status_code == 204
    assert (await client.get(path)).json() == [str(guest)]
    assert (await client.get(path+'?offset=1')).json() == []
    assert (await client.get(path+'?limit=0')).status_code == 422
    assert (await client.put(f'{path}/{uuid4()}')).status_code == 404
    authorize(client,guest)
    response=await client.post('/events',json=body(venue))
    assert response.status_code == 201
    event=response.json()
    assert event['capacity'] == 2 and event['venue_id'] == str(venue)
    authorize(client,owner)
    assert (await client.post(f'/venues/{venue}/seats',json={'seats':[{'section':'Main','row':'A','number':3}]})).status_code == 201
    own=await client.post('/events',json=body(venue))
    assert own.status_code == 201 and own.json()['capacity'] == 3
    for _ in range(2):
        assert (await client.delete(f'{path}/{guest}')).status_code == 204
    authorize(client,guest)
    assert (await client.post('/events',json=body(venue))).status_code == 403
    client.headers.clear()
    path=f"/events/{event['id']}/seats"
    assert [s['number'] for s in (await client.get(path)).json()] == [1,2]
    assert (await client.get(path+'?limit=1&offset=1')).json()[0]['number'] == 2
    assert (await client.get(path+'?limit=501')).status_code == 422
    assert (await client.get(f'/events/{uuid4()}/seats')).status_code == 404
    assert (await client.get('/events/not-a-uuid/seats')).status_code == 422
    assert (await client.get('/events/'+event['id'])).json()['capacity'] == 2


async def test_missing_empty_and_current_permission(setup):
    """Existing grants never bypass current organizer permission or seat requirements."""
    client, connection, owner, guest, venue=setup
    authorize(client,owner)
    assert (await client.post('/events',json=body(uuid4()))).status_code == 404
    empty=uuid4()
    await connection.execute(Venue.__table__.insert().values(id=empty,owner_id=owner,name='Empty',address='Street'))
    assert (await client.post('/events',json=body(empty))).status_code == 409
    assert (await client.get('/events')).json() == []
    assert (await client.put(f'/venues/{venue}/organizers/{guest}')).status_code == 204
    await connection.execute(update(User).where(User.id==guest).values(is_organizer=False))
    assert (await client.put(f'/venues/{venue}/organizers/{guest}')).status_code == 409
    authorize(client,guest)
    assert (await client.post('/events',json=body(venue))).status_code == 403
    authorize(client,owner)
    for field,value in [('venue','Hall'),('capacity',10)]:
        assert (await client.post('/events',json={**body(venue),field:value})).status_code == 422


async def test_snapshot_failure_rolls_back_event(setup,monkeypatch):
    """A failed membership insert must not leave a committed event behind."""
    client, connection, owner, _, venue=setup
    def invalid_membership(**kwargs):
        # Force an actual foreign-key failure after the event has been flushed.
        return EventSeat(**{**kwargs,'seat_id':uuid4()})
    monkeypatch.setattr(events,'EventSeat',invalid_membership)
    async with AsyncSession(bind=connection,join_transaction_mode='create_savepoint',expire_on_commit=False) as session:
        with pytest.raises(IntegrityError):
            await events.create_event(session,EventCreate(**body(venue)),organizer_id=owner)
    assert list((await connection.execute(select(Event.id))).scalars()) == []


async def test_composite_membership_constraint(setup):
    """Even direct SQL cannot attach another venue's seat to an event."""
    client, connection, owner, _, venue=setup
    authorize(client,owner)
    event=(await client.post('/events',json=body(venue))).json()
    other, seat_id=uuid4(),uuid4()
    await connection.execute(Venue.__table__.insert().values(id=other,owner_id=owner,name='Other',address='Street'))
    await connection.execute(Seat.__table__.insert().values(id=seat_id,venue_id=other,section='Main',row='A',number=1))
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(EventSeat.__table__.insert().values(event_id=UUID(event['id']),seat_id=seat_id,venue_id=venue))


@pytest.mark.parametrize('operation',['grant','revoke','seat'])
async def test_event_creation_waits_for_venue_changes(operation,monkeypatch):
    """Observe a real PostgreSQL lock wait, then verify the committed change is seen."""
    settings=Settings()
    url=str(settings.test_database_url)
    assert make_url(url).database=='venuepass_db_test'
    engine=create_async_engine(url)
    owner,guest,venue=uuid4(),uuid4(),uuid4()
    task=None
    try:
        async with engine.begin() as conn:
            for user in (owner,guest):
                await conn.execute(User.__table__.insert().values(id=user,email=f'{user}@example.com',password_hash='unused',is_organizer=True,is_venue_manager=True))
            await conn.execute(Venue.__table__.insert().values(id=venue,owner_id=owner,name='Race',address='Street'))
            await conn.execute(Seat.__table__.insert().values(venue_id=venue,section='Main',row='A',number=1))
            if operation!='grant':
                await conn.execute(VenueOrganizer.__table__.insert().values(venue_id=venue,organizer_id=guest))
        async with engine.connect() as blocker:
            transaction=await blocker.begin()
            await blocker.execute(select(Venue.id).where(Venue.id==venue).with_for_update())
            if operation=='grant':
                await blocker.execute(VenueOrganizer.__table__.insert().values(venue_id=venue,organizer_id=guest))
            elif operation=='revoke':
                await blocker.execute(delete(VenueOrganizer).where(VenueOrganizer.venue_id==venue))
            else:
                await blocker.execute(Seat.__table__.insert().values(venue_id=venue,section='Main',row='A',number=2))
            ready=asyncio.Event()
            pid=None
            async def create():
                nonlocal pid
                async with AsyncSession(engine,expire_on_commit=False) as session:
                    pid=await session.scalar(text('SELECT pg_backend_pid()'))
                    ready.set()
                    return await events.create_event(session,EventCreate(**body(venue)),organizer_id=guest)
            task=asyncio.create_task(create())
            await asyncio.wait_for(ready.wait(),5)
            # Do not infer concurrency from timing alone: require an observed lock wait.
            async with asyncio.timeout(5):
                while not await blocker.scalar(text('SELECT cardinality(pg_blocking_pids(:pid)) > 0'),{'pid':pid}):
                    if task.done():
                        pytest.fail('Event creation did not wait for the venue lock')
                    await asyncio.sleep(.01)
            await transaction.commit()
            if operation=='revoke':
                with pytest.raises(VenueAccessDenied):
                    await asyncio.wait_for(task,5)
            else:
                event=await asyncio.wait_for(task,5)
                assert event.capacity == (2 if operation=='seat' else 1)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        async with engine.begin() as conn:
            await conn.execute(delete(EventSeat).where(EventSeat.venue_id==venue))
            await conn.execute(delete(Event).where(Event.venue_id==venue))
            await conn.execute(delete(VenueOrganizer).where(VenueOrganizer.venue_id==venue))
            await conn.execute(delete(Seat).where(Seat.venue_id==venue))
            await conn.execute(delete(Venue).where(Venue.id==venue))
            await conn.execute(delete(User).where(User.id.in_([owner,guest])))
        await engine.dispose()
