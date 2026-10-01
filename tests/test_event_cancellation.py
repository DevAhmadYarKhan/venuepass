"""Organizer cancellation preserves history and serializes with customer operations."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import Settings
from app.main import create_app
from app.models import Event, EventSeat, Reservation, ReservationSeat, Seat, User, Venue, VenueOrganizer
from app.services import events as event_service
from test_reservations import SECRET, book, booking_data, headers, seed
from test_cancellation import cancel

pytestmark = pytest.mark.integration


async def cancel_event(client, user, event_id):
    """Submit an authenticated event cancellation with no request body."""
    return await client.post(f'/events/{event_id}/cancel', headers=headers(user))


async def test_event_cancellation_history_and_discovery(booking_data):
    """Cancel active bookings, preserve previous withdrawals, and stop future bookings."""
    client, conn, (users, venue, event_ids, seats, start) = booking_data
    await conn.execute(update(User).where(User.id == users[0]).values(is_organizer=True))
    first = (await book(client, users[0], event_ids[0], seats[:2])).json()
    withdrawn = (await book(client, users[1], event_ids[0], seats[2:3], 'withdrawn')).json()
    withdrawn = (await cancel(client, users[1], withdrawn['id'])).json()
    result = await cancel_event(client, users[0], event_ids[0])
    assert result.status_code == 200
    event = result.json()
    assert event['cancelled_at'] is not None
    assert (await cancel_event(client, users[0], event_ids[0])).json() == event
    assert (await client.get(f'/events/{event_ids[0]}')).json() == event
    detail = (await client.get(f"/reservations/{first['id']}", headers=headers(users[0]))).json()
    assert detail['cancellation_reason'] == 'event_cancelled'
    assert detail['cancelled_at'] == event['cancelled_at']
    assert detail['seat_ids'] == first['seat_ids']
    assert (await client.get(f"/reservations/{withdrawn['id']}", headers=headers(users[1]))).json() == withdrawn
    assert withdrawn['cancellation_reason'] == 'customer'
    assert (await client.get('/users/me/reservations', headers=headers(users[0]))).json() == [detail]
    retry = await book(client, users[0], event_ids[0], seats[:2])
    assert retry.status_code == 201 and retry.json() == detail
    assert (await book(client, users[0], event_ids[0], seats[3:4])).status_code == 409
    assert (await book(client, users[1], event_ids[0], seats[:2], 'new')).status_code == 409
    assert (await cancel(client, users[0], first['id'])).json() == detail
    assert all(not row['is_available'] for row in (await client.get(f'/events/{event_ids[0]}/seats')).json())
    assert [row['id'] for row in (await client.get('/events')).json()] == [str(event_ids[1])]
    included = (await client.get('/events', params={'include_cancelled': 'true', 'upcoming_only': 'true', 'q': 'Concert'})).json()
    assert {row['id'] for row in included} == {str(id_) for id_ in event_ids}
    claims = (await conn.execute(select(ReservationSeat.released_at).where(
        ReservationSeat.reservation_id == UUID(first['id'])))).scalars().all()
    assert all(value == datetime.fromisoformat(event['cancelled_at']) for value in claims)


async def test_permissions_and_revoked_venue_access(booking_data):
    """Only current organizers owning the event may cancel; venue access is irrelevant."""
    client, conn, (users, venue, event_ids, seats, start) = booking_data
    assert (await client.post(f'/events/{event_ids[0]}/cancel')).status_code == 401
    assert (await cancel_event(client, users[0], event_ids[0])).status_code == 403
    await conn.execute(update(User).where(User.id.in_(users)).values(is_organizer=True))
    assert (await cancel_event(client, users[1], event_ids[0])).status_code == 403
    assert (await cancel_event(client, users[0], uuid4())).status_code == 404
    # The event creator no longer owns the venue and has no explicit grant.
    await conn.execute(update(Venue).where(Venue.id == venue).values(owner_id=users[1]))
    await conn.execute(delete(VenueOrganizer).where(VenueOrganizer.venue_id == venue))
    assert (await cancel_event(client, users[0], event_ids[0])).status_code == 200
    await conn.execute(update(User).where(User.id == users[0]).values(is_organizer=False))
    assert (await cancel_event(client, users[0], event_ids[0])).status_code == 403


async def test_cutoff_and_empty_event(booking_data, monkeypatch):
    """No bookings are required; the strict cutoff is checked only on the first cancellation."""
    client, conn, (users, venue, event_ids, seats, start) = booking_data
    await conn.execute(update(User).where(User.id == users[0]).values(is_organizer=True))
    monkeypatch.setattr(event_service, 'utc_now', lambda: start - timedelta(microseconds=1))
    cancelled = (await cancel_event(client, users[0], event_ids[0])).json()
    monkeypatch.setattr(event_service, 'utc_now', lambda: start)
    assert (await cancel_event(client, users[0], event_ids[1])).status_code == 409
    assert (await cancel_event(client, users[0], event_ids[0])).json() == cancelled
    assert await conn.scalar(select(Event.cancelled_at).where(Event.id == event_ids[1])) is None


async def test_atomic_rollback(booking_data, monkeypatch):
    """A failure after all writes restores the event, booking, and claim together."""
    client, conn, (users, venue, event_ids, seats, start) = booking_data
    await conn.execute(update(User).where(User.id == users[0]).values(is_organizer=True))
    original = (await book(client, users[0], event_ids[0], seats[:1])).json()
    async with AsyncSession(bind=conn, join_transaction_mode='create_savepoint', expire_on_commit=False) as session:
        async def fail_commit():
            """Flush all writes before simulating failure to commit."""
            await session.flush()
            raise RuntimeError('simulated commit failure')
        monkeypatch.setattr(session, 'commit', fail_commit)
        with pytest.raises(RuntimeError, match='simulated commit failure'):
            await event_service.cancel_event(session, event_ids[0], organizer_id=users[0])
    assert await conn.scalar(select(Event.cancelled_at).where(Event.id == event_ids[0])) is None
    assert (await client.get(f"/reservations/{original['id']}", headers=headers(users[0]))).json() == original
    assert await conn.scalar(select(ReservationSeat.released_at).where(ReservationSeat.event_id == event_ids[0])) is None


@pytest.mark.parametrize('cancelled_at,reason', [
    (None, 'customer'), (datetime.now(timezone.utc), None),
    (datetime.now(timezone.utc), 'unknown'),
])
async def test_cancellation_consistency_constraint(booking_data, cancelled_at, reason):
    """Direct SQL cannot record a timestamp and reason that disagree about state."""
    client, conn, (users, venue, event_ids, seats, start) = booking_data
    with pytest.raises(IntegrityError, match='ck_reservations_cancellation'):
        async with conn.begin_nested():
            await conn.execute(Reservation.__table__.insert().values(
                event_id=event_ids[0], user_id=users[0], idempotency_key='direct',
                cancelled_at=cancelled_at, cancellation_reason=reason))


@pytest.mark.parametrize('scenario', ['repeat', 'customer', 'retry', 'booking', 'started'])
async def test_concurrent_event_cancellation(scenario):
    """Observe real event-row waits for event cancellation and competing customer operations."""
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
                    tasks.append(asyncio.create_task(cancel_event(client, users[0], events[0])))
                    if scenario in ('repeat', 'started'):
                        tasks.append(asyncio.create_task(cancel_event(client, users[0], events[0])))
                    elif scenario == 'customer':
                        tasks.append(asyncio.create_task(cancel(client, users[0], original['id'])))
                    else:
                        tasks.append(asyncio.create_task(book(client, users[0], events[0],
                            seats[:2] if scenario == 'retry' else seats[2:3],
                            'same' if scenario == 'retry' else 'new')))
                    # All requests must reach PostgreSQL and wait, rather than merely start together.
                    async with asyncio.timeout(10):
                        while True:
                            assert not any(task.done() for task in tasks)
                            if len(request_pids) == 2:
                                waits = [await blocker.scalar(text('SELECT cardinality(pg_blocking_pids(:pid)) > 0'), {'pid': pid}) for pid in request_pids]
                                if all(waits):
                                    break
                            await asyncio.sleep(.01)
                    if scenario=='started':
                        await blocker.execute(update(Event).where(Event.id==events[0]).values(starts_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
                    await transaction.commit()
                responses = await asyncio.wait_for(asyncio.gather(*tasks),10)
                if scenario == 'started':
                    assert [r.status_code for r in responses] == [409, 409]
                else:
                    assert responses[0].status_code == 200
                    if scenario == 'repeat':
                        assert responses[1].status_code == 200
                        assert responses[0].json() == responses[1].json()
                    elif scenario == 'customer':
                        assert responses[1].status_code == 200
                    elif scenario == 'retry':
                        assert responses[1].status_code == 201
                        assert responses[1].json()['id'] == original['id']
                    else:
                        # A booking that commits first must be swept up by cancellation.
                        assert responses[1].status_code in (201, 409)
                async with engine.connect() as conn:
                    claims = list((await conn.execute(select(ReservationSeat.seat_id).where(
                        ReservationSeat.event_id == events[0], ReservationSeat.released_at.is_(None)))).scalars())
                    bookings = (await conn.execute(select(Reservation.cancelled_at, Reservation.cancellation_reason).where(
                        Reservation.event_id == events[0]))).all()
                    cancelled_at = await conn.scalar(select(Event.cancelled_at).where(Event.id == events[0]))
                assert (cancelled_at is None) == (scenario == 'started')
                assert len(claims) == (2 if scenario == 'started' else 0)
                if scenario != 'started':
                    assert all(timestamp is not None and reason in ('customer', 'event_cancelled') for timestamp, reason in bookings)
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
