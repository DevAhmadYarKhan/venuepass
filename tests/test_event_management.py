"""Organizer listings and partial edits preserve event lifecycle and booking data."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import Settings
from app.main import create_app
from app.models import Event, EventSeat, Reservation, ReservationSeat, Seat, User, Venue
from app.schemas.events import EventUpdate
from app.services import events as event_service
from test_reservations import SECRET, book, booking_data, headers, seed
from test_event_cancellation import cancel_event

pytestmark = pytest.mark.integration


async def edit(client, user, event_id, payload):
    """Exercise patch requests with real bearer authentication."""
    return await client.patch(f'/events/{event_id}', headers=headers(user), json=payload)


async def test_owned_listing(booking_data):
    """Include owned past/cancelled events with stable newest-first pagination."""
    client, conn, (users, venue, event_ids, seats, start) = booking_data
    assert (await client.get('/users/me/events')).status_code == 401
    assert (await client.get('/users/me/events', headers=headers(users[0]))).status_code == 403
    await conn.execute(update(User).where(User.id.in_(users)).values(is_organizer=True))
    # Equal creation timestamps exercise descending UUID ties, independent of start time.
    await conn.execute(update(Event).where(Event.id.in_(event_ids)).values(created_at=start,
        starts_at=datetime.now(timezone.utc)-timedelta(days=1)))
    await conn.execute(update(Event).where(Event.id == event_ids[0]).values(cancelled_at=start))
    response = await client.get('/users/me/events', headers=headers(users[0]))
    assert response.status_code == 200
    expected = sorted([str(id_) for id_ in event_ids], reverse=True)
    assert [row['id'] for row in response.json()] == expected
    page = await client.get('/users/me/events?limit=1&offset=1', headers=headers(users[0]))
    assert [row['id'] for row in page.json()] == expected[1:]
    assert (await client.get('/users/me/events?offset=2', headers=headers(users[0]))).json() == []
    assert (await client.get('/users/me/events', headers=headers(users[1]))).json() == []
    await conn.execute(update(Event).where(Event.id == event_ids[0]).values(created_at=start+timedelta(seconds=1)))
    newest = (await client.get('/users/me/events', headers=headers(users[0]))).json()
    assert newest[0]['id'] == str(event_ids[0])
    await conn.execute(update(Event).where(Event.id == event_ids[0]).values(organizer_id=users[1]))
    assert len((await client.get('/users/me/events', headers=headers(users[0]))).json()) == 1
    for query in ('limit=0', 'limit=101', 'offset=-1'):
        assert (await client.get('/users/me/events?'+query, headers=headers(users[0]))).status_code == 422


async def test_edits_preserve_omitted_fields_and_bookings(booking_data):
    """Names are trimmed, null clears descriptions, and unrelated state stays fixed."""
    client, conn, (users, venue, event_ids, seats, start) = booking_data
    await conn.execute(update(User).where(User.id == users[0]).values(is_organizer=True))
    booking = (await book(client, users[0], event_ids[0], seats[:2])).json()
    original = (await client.get(f'/events/{event_ids[0]}')).json()
    response = await edit(client, users[0], event_ids[0], {'name': '  Renamed  ', 'description': 'Details'})
    assert response.status_code == 200
    assert response.json() == {**original, 'name': 'Renamed', 'description': 'Details'}
    response = await edit(client, users[0], event_ids[0], {'name': 'Again'})
    assert response.json()['description'] == 'Details'
    response = await edit(client, users[0], event_ids[0], {'description': ''})
    assert response.json()['name'] == 'Again' and response.json()['description'] == ''
    response = await edit(client, users[0], event_ids[0], {'description': None})
    assert response.json() == {**original, 'name': 'Again', 'description': None}
    assert (await client.get(f"/reservations/{booking['id']}", headers=headers(users[0]))).json() == booking
    assert len((await conn.execute(select(EventSeat.seat_id).where(EventSeat.event_id == event_ids[0]))).all()) == len(seats)


@pytest.mark.parametrize('payload', [
    {}, {'name': None}, {'name': ''}, {'name': ' \t '}, {'name': 'x'*256},
    {'name': 42}, {'description': 42}, {'venue_id': str(uuid4())},
    {'starts_at': '2099-01-01T00:00:00Z'}, {'ends_at': None}, {'capacity': 1},
    {'organizer_id': str(uuid4())}, {'cancelled_at': None},
    {'name': 'valid', 'unsupported': True},
])
async def test_invalid_patches(booking_data, payload):
    """Reject empty patches and unsupported fields instead of silently ignoring them."""
    client, conn, (users, venue, event_ids, seats, start) = booking_data
    await conn.execute(update(User).where(User.id == users[0]).values(is_organizer=True))
    original = (await client.get(f'/events/{event_ids[0]}')).json()
    assert (await edit(client, users[0], event_ids[0], payload)).status_code == 422
    assert (await client.get(f'/events/{event_ids[0]}')).json() == original


async def test_permissions_cutoff_and_revoked_access(booking_data, monkeypatch):
    """Ownership survives venue-access loss, but editing requires permission and an upcoming event."""
    client, conn, (users, venue, event_ids, seats, start) = booking_data
    assert (await client.patch(f'/events/{event_ids[0]}', json={'name': 'New'})).status_code == 401
    assert (await edit(client, users[0], event_ids[0], {'name': 'New'})).status_code == 403
    await conn.execute(update(User).where(User.id.in_(users)).values(is_organizer=True))
    assert (await edit(client, users[1], event_ids[0], {'name': 'New'})).status_code == 403
    assert (await edit(client, users[0], uuid4(), {'name': 'New'})).status_code == 404
    await conn.execute(update(Venue).where(Venue.id == venue).values(owner_id=users[1]))
    monkeypatch.setattr(event_service, 'utc_now', lambda: start-timedelta(microseconds=1))
    assert (await edit(client, users[0], event_ids[0], {'name': 'New'})).status_code == 200
    monkeypatch.setattr(event_service, 'utc_now', lambda: start)
    assert (await edit(client, users[0], event_ids[0], {'name': 'Too late'})).status_code == 409
    await conn.execute(update(Event).where(Event.id == event_ids[1]).values(cancelled_at=start))
    monkeypatch.setattr(event_service, 'utc_now', lambda: start-timedelta(days=1))
    assert (await edit(client, users[0], event_ids[1], {'name': 'Cancelled'})).status_code == 409


async def test_failed_edit_rolls_back(booking_data, monkeypatch):
    """A commit failure restores both descriptive fields."""
    client, conn, (users, venue, event_ids, seats, start) = booking_data
    await conn.execute(update(User).where(User.id == users[0]).values(is_organizer=True))
    original = (await client.get(f'/events/{event_ids[0]}')).json()
    async with AsyncSession(bind=conn, join_transaction_mode='create_savepoint', expire_on_commit=False) as session:
        async def fail_commit():
            """Flush actual writes before forcing rollback."""
            await session.flush()
            raise RuntimeError('simulated failure')
        monkeypatch.setattr(session, 'commit', fail_commit)
        with pytest.raises(RuntimeError, match='simulated failure'):
            await event_service.update_event(session, event_ids[0], EventUpdate(name='New', description='New'), organizer_id=users[0])
    assert (await client.get(f'/events/{event_ids[0]}')).json() == original


@pytest.mark.parametrize('scenario', ['cancel', 'started'])
async def test_concurrent_edit_and_cancel(scenario):
    """Observe editing wait on the event row before checking cancellation or start time."""
    settings = Settings()
    url = str(settings.test_database_url)
    assert make_url(url).database == 'venuepass_db_test'
    engine = create_async_engine(url)
    data = None
    tasks = []
    try:
        async with engine.begin() as conn:
            data = await seed(conn)
            await conn.execute(update(User).where(User.id == data[0][0]).values(is_organizer=True))
        users, venue, events, seats, start = data
        app = create_app(Settings(database_url=url, jwt_secret=SECRET, _env_file=None))
        from app.database import get_session
        request_pids = []
        async def tracked_session():
            """Identify each real request connection before it reaches the event lock."""
            async with app.state.session_factory() as session:
                request_pids.append(await session.scalar(text('SELECT pg_backend_pid()')))
                yield session
        app.dependency_overrides[get_session] = tracked_session
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
                original = (await book(client, users[0], events[0], seats[:2], 'same')).json()
                request_pids.clear()
                async with engine.connect() as blocker:
                    transaction = await blocker.begin()
                    await blocker.execute(select(Event.id).where(Event.id==events[0]).with_for_update())
                    tasks.append(asyncio.create_task(edit(client, users[0], events[0], {'name': 'Updated'})))
                    if scenario == 'cancel':
                        tasks.append(asyncio.create_task(cancel_event(client, users[0], events[0])))
                    # All requests must reach PostgreSQL and wait, rather than merely start together.
                    async with asyncio.timeout(10):
                        while True:
                            assert not any(task.done() for task in tasks)
                            if len(request_pids) == (2 if scenario == 'cancel' else 1):
                                waits = [await blocker.scalar(text('SELECT cardinality(pg_blocking_pids(:pid)) > 0'), {'pid': pid}) for pid in request_pids]
                                if all(waits):
                                    break
                            await asyncio.sleep(.01)
                    if scenario=='started':
                        await blocker.execute(update(Event).where(Event.id==events[0]).values(starts_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
                    await transaction.commit()
                responses = await asyncio.wait_for(asyncio.gather(*tasks),10)
                if scenario == 'started':
                    assert responses[0].status_code == 409
                else:
                    # Editing may precede cancellation, but cannot succeed after it.
                    assert responses[0].status_code in (200, 409)
                    assert responses[1].status_code == 200
                async with engine.connect() as conn:
                    stored = (await conn.execute(select(Event.name, Event.cancelled_at).where(Event.id == events[0]))).one()
                assert stored.name == ('Updated' if responses[0].status_code == 200 else 'Concert')
                assert (stored.cancelled_at is not None) == (scenario == 'cancel')
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if data:
            users, venue, events, seats, start = data
            async with engine.begin() as conn:
                for model in [ReservationSeat, Reservation, EventSeat]:
                    await conn.execute(delete(model).where(model.event_id.in_(events)))
                await conn.execute(delete(Event).where(Event.id.in_(events)))
                await conn.execute(delete(Seat).where(Seat.venue_id==venue))
                await conn.execute(delete(Venue).where(Venue.id==venue))
                await conn.execute(delete(User).where(User.id.in_(users)))
        await engine.dispose()
