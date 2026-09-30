"""Bookings, retry recovery, ownership, and real PostgreSQL concurrency checks."""

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
from app.security import create_access_token
from app.services import reservations

pytestmark = pytest.mark.integration
SECRET = 'test-signing-secret-that-is-at-least-32-characters'


async def seed(connection):
    """Create two customers, one venue, and two events sharing physical seats."""
    users = [uuid4(), uuid4()]
    venue = uuid4()
    events = [uuid4(), uuid4()]
    seats = [uuid4() for _ in range(21)]
    start = datetime.now(timezone.utc) + timedelta(days=2)
    await connection.execute(User.__table__.insert(), [
        {'id': user, 'email': f'{user}@example.com', 'password_hash': 'unused'} for user in users
    ])
    await connection.execute(Venue.__table__.insert().values(id=venue, owner_id=users[0], name='Booking hall', address='Street'))
    await connection.execute(Seat.__table__.insert(), [
        {'id': seat, 'venue_id': venue, 'section': 'Main', 'row': 'A', 'number': n+1}
        for n, seat in enumerate(seats)
    ])
    await connection.execute(Event.__table__.insert(), [
        {'id': event, 'organizer_id': users[0], 'venue_id': venue, 'name': 'Concert', 'starts_at': start, 'capacity': len(seats)} for event in events
    ])
    await connection.execute(EventSeat.__table__.insert(), [
        {'event_id': event, 'seat_id': seat, 'venue_id': venue} for event in events for seat in seats
    ])
    return users, venue, events, seats, start


def headers(user, key='booking-one'):
    """Authenticate an ordinary user and identify one intended booking."""
    return {'Authorization': 'Bearer '+create_access_token(user, SECRET), 'Idempotency-Key': key}


async def book(client, user, event, seats, key='booking-one'):
    """Submit explicit seat IDs through the real HTTP route."""
    return await client.post(f'/events/{event}/reservations', headers=headers(user, key), json={'seat_ids': [str(s) for s in seats]})


@pytest.fixture
async def booking_data(event_client):
    """Reuse savepoint isolation for ordinary endpoint tests."""
    client, connection = event_client
    return client, connection, await seed(connection)


async def test_booking_retries_ownership_history_and_availability(booking_data):
    """Successful retries are stable, private bookings remain private, and availability changes."""
    client, connection, (users, venue, events, seats, start) = booking_data
    before = (await client.get(f'/events/{events[0]}/seats')).json()
    assert all(s['is_available'] for s in before)
    response = await book(client, users[0], events[0], seats[:2])
    assert response.status_code == 201
    original = response.json()
    assert set(original) == {'id', 'event_id', 'user_id', 'seat_ids', 'created_at'}
    assert original['seat_ids'] == sorted(str(s) for s in seats[:2])
    retry = await book(client, users[0], events[0], list(reversed(seats[:2])))
    assert retry.status_code == 201 and retry.json() == original
    assert (await book(client, users[0], events[0], seats[2:3])).status_code == 409
    # Failed keys are reusable, and a losing multi-seat request cannot claim its free seat.
    assert (await book(client, users[1], events[0], seats[1:3], 'failed')).status_code == 409
    assert (await book(client, users[1], events[0], seats[2:3], 'failed')).status_code == 201
    assert (await book(client, users[0], events[0], seats[3:4], 'second')).status_code == 201
    path = '/reservations/'+original['id']
    assert (await client.get(path, headers=headers(users[0]))).json() == original
    assert (await client.get(path, headers=headers(users[1]))).status_code == 404
    assert (await client.get('/reservations/'+str(uuid4()), headers=headers(users[0]))).status_code == 404
    assert (await client.get(path)).status_code == 401
    history = (await client.get('/users/me/reservations', headers=headers(users[0]))).json()
    assert len(history) == 2
    assert history == sorted(history, key=lambda b: (b['created_at'], b['id']), reverse=True)
    page = (await client.get('/users/me/reservations?limit=1&offset=1', headers=headers(users[0]))).json()
    assert page == history[1:2]
    assert (await client.get('/users/me/reservations')).status_code == 401
    for query in ['limit=0', 'limit=101', 'offset=-1']:
        assert (await client.get('/users/me/reservations?'+query, headers=headers(users[0]))).status_code == 422
    availability = (await client.get(f'/events/{events[0]}/seats')).json()
    assert [s['is_available'] for s in availability[:5]] == [False,False,False,False,True]
    assert all('user_id' not in s for s in availability)


async def test_key_scope_and_time_boundaries(booking_data, monkeypatch):
    """Keys are per user/event; past-event retries recover successful bookings."""
    client, connection, (users, venue, events, seats, start) = booking_data
    first = await book(client, users[0], events[0], seats[:1], 'shared')
    assert first.status_code == 201
    assert (await book(client, users[1], events[0], seats[1:2], 'shared')).status_code == 201
    assert (await book(client, users[0], events[1], seats[:1], 'shared')).status_code == 201
    monkeypatch.setattr(reservations, 'utc_now', lambda: start)
    assert (await book(client, users[0], events[0], seats[2:3], 'new')).status_code == 409
    retry = await book(client, users[0], events[0], seats[:1], 'shared')
    assert retry.status_code == 201 and retry.json() == first.json()
    await connection.execute(update(Event).where(Event.id == events[0]).values(starts_at=datetime.now(timezone.utc)-timedelta(days=1)))
    assert not any(s['is_available'] for s in (await client.get(f'/events/{events[0]}/seats')).json())


async def test_request_validation_and_failed_retry(booking_data):
    """Missing keys, malformed IDs, invalid membership, and duplicates fail cleanly."""
    client, connection, (users, venue, events, seats, start) = booking_data
    path = f'/events/{events[0]}/reservations'
    payload = {'seat_ids': [str(seats[0])]}
    assert (await client.post(path, json=payload, headers={'Idempotency-Key':'key'})).status_code == 401
    auth = headers(users[0]); auth.pop('Idempotency-Key')
    assert (await client.post(path, json=payload, headers=auth)).status_code == 422
    for key in ['', 'with space', 'x'*129]:
        assert (await book(client, users[0], events[0], seats[:1], key)).status_code == 422
    for ids in [[], [seats[0],seats[0]], seats, ['bad-uuid']]:
        assert (await book(client, users[0], events[0], ids)).status_code == 422
    assert (await book(client, users[0], uuid4(), seats[:1])).status_code == 404
    assert (await book(client, users[0], 'invalid', seats[:1])).status_code == 422
    # A physical seat added after event creation is not a member of this event.
    extra = uuid4()
    await connection.execute(Seat.__table__.insert().values(id=extra,venue_id=venue,section='Main',row='B',number=1))
    assert (await book(client, users[0], events[0], [extra], 'retry')).status_code == 422
    assert (await book(client, users[0], events[0], seats[:20], 'retry')).status_code == 201


async def test_failure_rolls_back_booking_and_key(booking_data, monkeypatch):
    """A database failure after reservation insertion leaves neither booking nor key."""
    client, connection, (users, venue, events, seats, start) = booking_data
    from sqlalchemy import event as sa_event
    def invalid_seat(mapper, connection, target):
        # Corrupt only the inserted claim, leaving earlier ORM queries intact.
        target.seat_id = uuid4()
    sa_event.listen(ReservationSeat, 'before_insert', invalid_seat)
    try:
        async with AsyncSession(bind=connection,join_transaction_mode='create_savepoint',expire_on_commit=False) as session:
            with pytest.raises(IntegrityError):
                await reservations.create_reservation(session, events[0], users[0], seats[:1], 'retry')
    finally:
        sa_event.remove(ReservationSeat, 'before_insert', invalid_seat)
    assert list((await connection.execute(select(Reservation.id))).scalars()) == []
    assert (await book(client, users[0], events[0], seats[:1], 'retry')).status_code == 201


async def test_database_constraints(booking_data):
    """Direct SQL cannot double-book or attach a claim to the wrong reservation event."""
    client, connection, (users, venue, events, seats, start) = booking_data
    first = (await book(client, users[0], events[0], seats[:1])).json()
    other = (await book(client, users[1], events[1], seats[1:2])).json()
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(ReservationSeat.__table__.insert().values(reservation_id=UUID(other['id']), event_id=events[0],seat_id=seats[2]))
    second = (await book(client, users[1], events[0], seats[3:4], 'second')).json()
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(ReservationSeat.__table__.insert().values(reservation_id=UUID(second['id']), event_id=events[0],seat_id=seats[0]))


@pytest.mark.parametrize('scenario', ['compete','identical','changed','started'])
async def test_concurrent_bookings(scenario):
    """Observe actual lock waits before releasing simultaneous HTTP booking requests."""
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
                async with engine.connect() as blocker:
                    transaction = await blocker.begin()
                    await blocker.execute(select(Event.id).where(Event.id==events[0]).with_for_update())
                    tasks.append(asyncio.create_task(book(client,users[0],events[0],seats[:2],'same')))
                    other_user = users[1] if scenario=='compete' else users[0]
                    other_seats = list(reversed(seats[:2])) if scenario=='identical' else seats[1:3]
                    tasks.append(asyncio.create_task(book(client,other_user,events[0],other_seats,'same')))
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
                expected = [201,201] if scenario=='identical' else [409,409] if scenario=='started' else [201,409]
                assert sorted(r.status_code for r in responses) == expected
                if scenario=='identical':
                    assert responses[0].json() == responses[1].json()
                async with engine.connect() as conn:
                    stored = list((await conn.execute(select(Reservation.id).where(Reservation.event_id==events[0]))).scalars())
                    claims = set((await conn.execute(select(ReservationSeat.seat_id).where(ReservationSeat.event_id==events[0]))).scalars())
                assert len(stored) == (0 if scenario=='started' else 1)
                if scenario!='started':
                    winner = next(r.json() for r in responses if r.status_code==201)
                    assert claims == {UUID(s) for s in winner['seat_ids']}
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
