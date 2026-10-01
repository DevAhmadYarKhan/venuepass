"""Cancellation preserves history and retry identity while releasing seat claims."""

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
from app.models import Event, EventSeat, Reservation, ReservationSeat, Seat, User, Venue
from app.services import reservations
from test_reservations import SECRET, book, booking_data, headers, seed

pytestmark = pytest.mark.integration


async def cancel(client, user, reservation_id):
    """Cancel through the authenticated route without a separate retry key."""
    return await client.post(f'/reservations/{reservation_id}/cancel', headers=headers(user))


async def test_cancellation_history_retries_and_rebooking(booking_data):
    """Original requests stay cancelled even after another user takes the seats."""
    client, conn, (users, venue, events, seats, start) = booking_data
    original = (await book(client, users[0], events[0], seats[:2])).json()
    assert original['cancelled_at'] is None
    path = f"/reservations/{original['id']}/cancel"
    assert (await client.post(path)).status_code == 401
    assert (await cancel(client, users[1], original['id'])).status_code == 404
    assert (await cancel(client, users[0], uuid4())).status_code == 404
    result = await cancel(client, users[0], original['id'])
    assert result.status_code == 200
    cancelled = result.json()
    assert cancelled['cancelled_at'] is not None
    assert cancelled['seat_ids'] == original['seat_ids']
    assert (await cancel(client, users[0], original['id'])).json() == cancelled
    availability = (await client.get(f'/events/{events[0]}/seats')).json()
    assert all(seat['is_available'] for seat in availability)
    assert (await book(client, users[1], events[0], seats[:2], 'replacement')).status_code == 201
    retry = await book(client, users[0], events[0], list(reversed(seats[:2])))
    assert retry.status_code == 201 and retry.json() == cancelled
    assert (await book(client, users[0], events[0], seats[2:3])).status_code == 409
    assert (await client.get(f"/reservations/{original['id']}", headers=headers(users[0]))).json() == cancelled
    assert (await client.get('/users/me/reservations', headers=headers(users[0]))).json() == [cancelled]
    claims = (await conn.execute(select(ReservationSeat.released_at).where(
        ReservationSeat.reservation_id == UUID(original['id'])))).scalars().all()
    assert len(claims) == 2 and all(value is not None for value in claims)


async def test_cancellation_cutoff_and_repeat_after_start(booking_data, monkeypatch):
    """The cutoff applies to active bookings, never to completed cancellation retries."""
    client, conn, (users, venue, events, seats, start) = booking_data
    first = (await book(client, users[0], events[0], seats[:1])).json()
    second = (await book(client, users[0], events[0], seats[1:2], 'second')).json()
    # Freeze the service clock to test the exact boundary without timing races.
    monkeypatch.setattr(reservations, 'utc_now', lambda: start - timedelta(microseconds=1))
    cancelled = (await cancel(client, users[0], first['id'])).json()
    monkeypatch.setattr(reservations, 'utc_now', lambda: start)
    assert (await cancel(client, users[0], second['id'])).status_code == 409
    assert (await cancel(client, users[0], first['id'])).json() == cancelled
    assert (await book(client, users[0], events[0], seats[:1])).json() == cancelled
    assert await conn.scalar(select(Reservation.cancelled_at).where(Reservation.id == UUID(second['id']))) is None


async def test_cancellation_failure_rolls_back_every_change(booking_data, monkeypatch):
    """A failed write must not leave either a cancelled booking or released claims."""
    client, conn, (users, venue, events, seats, start) = booking_data
    original = (await book(client, users[0], events[0], seats[:2])).json()
    async with AsyncSession(bind=conn, join_transaction_mode='create_savepoint', expire_on_commit=False) as session:
        async def fail_flush(*args, **kwargs):
            """Fail the explicit async flush after the claim update; autoflush is synchronous."""
            raise RuntimeError('simulated write failure')
        monkeypatch.setattr(session, 'flush', fail_flush)
        with pytest.raises(RuntimeError, match='simulated write failure'):
            await reservations.cancel_reservation(session, UUID(original['id']), users[0])
    assert await conn.scalar(select(Reservation.cancelled_at).where(Reservation.id == UUID(original['id']))) is None
    assert all(value is None for value in (await conn.execute(select(ReservationSeat.released_at).where(
        ReservationSeat.reservation_id == UUID(original['id'])))).scalars())


async def test_partial_index_allows_history_but_rejects_two_active_claims(booking_data):
    """Database enforcement remains effective independently of service locking."""
    client, conn, (users, venue, events, seats, start) = booking_data
    original = (await book(client, users[0], events[0], seats[:1])).json()
    await cancel(client, users[0], original['id'])
    assert (await book(client, users[0], events[0], seats[:1], 'replacement')).status_code == 201
    another = uuid4()
    await conn.execute(Reservation.__table__.insert().values(id=another, event_id=events[0], user_id=users[0], idempotency_key='direct'))
    with pytest.raises(IntegrityError, match='uq_reservation_seats_event_seat'):
        async with conn.begin_nested():
            await conn.execute(ReservationSeat.__table__.insert().values(reservation_id=another, event_id=events[0], seat_id=seats[0]))


@pytest.mark.parametrize('scenario', ['repeat', 'retry', 'rebook', 'started'])
async def test_concurrent_cancellations(scenario):
    """Observe cancellation, retries, and new bookings waiting on the same event lock."""
    settings = Settings()
    url = str(settings.test_database_url)
    assert make_url(url).database == 'venuepass_db_test'
    engine = create_async_engine(url)
    data = None
    tasks = []
    try:
        async with engine.begin() as conn:
            data = await seed(conn)
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
                    tasks.append(asyncio.create_task(cancel(client, users[0], original['id'])))
                    if scenario in ('repeat', 'started'):
                        tasks.append(asyncio.create_task(cancel(client, users[0], original['id'])))
                    else:
                        tasks.append(asyncio.create_task(book(
                            client, users[0] if scenario == 'retry' else users[1],
                            events[0], seats[:2], 'same' if scenario == 'retry' else 'new')))
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
                elif scenario == 'repeat':
                    assert [r.status_code for r in responses] == [200, 200]
                    assert responses[0].json() == responses[1].json()
                elif scenario == 'retry':
                    # A retry may linearize before cancellation, but never creates a booking.
                    assert [r.status_code for r in responses] == [200, 201]
                    assert responses[1].json()['id'] == original['id']
                else:
                    # Rebooking either sees the occupied seat or succeeds after release.
                    assert responses[0].status_code == 200
                    assert responses[1].status_code in (201, 409)
                async with engine.connect() as conn:
                    active = list((await conn.execute(select(ReservationSeat.seat_id).where(
                        ReservationSeat.event_id == events[0], ReservationSeat.released_at.is_(None)))).scalars())
                    cancelled_at = await conn.scalar(select(Reservation.cancelled_at).where(
                        Reservation.id == UUID(original['id'])))
                assert (cancelled_at is None) == (scenario == 'started')
                expected_active = 2 if scenario == 'started' or (scenario == 'rebook' and responses[1].status_code == 201) else 0
                assert len(active) == expected_active
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
