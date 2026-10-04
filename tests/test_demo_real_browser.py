"""A real browser-to-PostgreSQL journey verifies API wiring, conflicts, and lost responses."""

import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import httpx
from playwright.sync_api import expect
import pytest
from sqlalchemy import delete, update, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.models import Event, EventSeat, Reservation, ReservationSeat, Seat, User, Venue, VenueOrganizer
from app.security import hash_password, create_access_token
from test_demo_browser import page
from test_reservations import SECRET, seed

pytestmark = [pytest.mark.integration, pytest.mark.browser,
              pytest.mark.skipif(os.getenv('VENUEPASS_BROWSER_TESTS') != '1', reason='Set VENUEPASS_BROWSER_TESTS=1 to run Chromium tests')]


@pytest.fixture
def demo_url():
    """Commit isolated fixtures for browser requests and always remove them afterward."""
    url = str(Settings().test_database_url)
    assert make_url(url).database == 'venuepass_db_test'
    data = None
    process = None
    password = 'a sufficiently long browser password'

    async def prepare():
        """Create real login credentials and a uniquely identifiable event."""
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                data = await seed(conn)
                await conn.execute(update(User).where(User.id.in_(data[0])).values(password_hash=await hash_password(password)))
                await conn.execute(update(User).where(User.id == data[0][0]).values(is_organizer=True))
                await conn.execute(update(Event).where(Event.id == data[2][0]).values(name='Browser concert'))
                return data
        finally:
            await engine.dispose()

    async def cleanup():
        """Remove only this fixture's rows in foreign-key dependency order."""
        if data is None:
            return
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                users, venue, events, seats, start = data
                # Management journeys create additional owned venues/events. Discover
                # only resources belonging to these unique fixture accounts.
                venue_ids = list((await conn.execute(select(Venue.id).where(
                    Venue.owner_id.in_(users)))).scalars())
                event_ids = list((await conn.execute(select(Event.id).where(
                    Event.venue_id.in_(venue_ids)))).scalars())
                for model in (ReservationSeat, Reservation, EventSeat):
                    await conn.execute(delete(model).where(model.event_id.in_(event_ids)))
                await conn.execute(delete(Event).where(Event.id.in_(event_ids)))
                await conn.execute(delete(VenueOrganizer).where(VenueOrganizer.venue_id.in_(venue_ids)))
                await conn.execute(delete(Seat).where(Seat.venue_id.in_(venue_ids)))
                await conn.execute(delete(Venue).where(Venue.id.in_(venue_ids)))
                await conn.execute(delete(User).where(User.id.in_(users)))
        finally:
            await engine.dispose()

    try:
        data = asyncio.run(prepare())
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {**os.environ, 'DATABASE_URL': url, 'JWT_SECRET': SECRET}
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port), '--log-level', 'error'], env=env, cwd=Path(__file__).resolve().parents[1])
        address = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urlopen(address+'/health', timeout=.2):
                    break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError('Real demo server exited')
                time.sleep(.05)
        else:
            raise RuntimeError('Real demo server did not start')
        yield {'url': address, 'data': data, 'password': password, 'database_url': url}
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=5)
        asyncio.run(cleanup())


def test_real_booking_retry_conflict_and_cancellation(page, demo_url):
    """A lost successful response recovers one booking; server conflicts and cancellation surface correctly."""
    address = demo_url['url']
    users, venue, events, seats, start = demo_url['data']
    page.goto(address+'/demo/')
    page.locator('#account').click()
    page.get_by_label('Email', exact=True).fill(f'{users[0]}@example.com')
    page.get_by_label('Password', exact=True).fill(demo_url['password'])
    page.locator('#auth-submit').click()
    expect(page.locator('#identity')).to_have_text(f'{users[0]}@example.com')
    page.get_by_label('Event name', exact=True).fill('Browser concert')
    page.get_by_role('button', name='Find events').click()
    expect(page.locator('.event-card')).to_have_count(1)
    page.get_by_role('button', name='View event & seats').click()
    expect(page.locator('#seat-status')).to_contain_text('21 of 21')
    attempts = []
    def lose_first_response(route):
        """Actually commit through the API, then drop the response reaching the browser."""
        attempts.append((route.request.headers['idempotency-key'], route.request.post_data_json))
        if len(attempts) == 1:
            response = route.fetch()
            assert response.status == 201
            route.abort()
        else:
            route.continue_()
    page.route(f'**/events/{events[0]}/reservations', lose_first_response)
    page.get_by_role('button', name='Main, Row A, Seat 1: available', exact=True).click()
    page.locator('#reserve').click()
    expect(page.locator('#booking-status')).to_contain_text('outcome is uncertain')
    page.locator('#retry-booking').click()
    expect(page.locator('#booking-status')).to_contain_text('Reservation confirmed')
    assert attempts[0] == attempts[1]
    expect(page.locator('.reservation-card')).to_have_count(1)
    expect(page.get_by_role('button', name='Main, Row A, Seat 1: unavailable', exact=True)).to_be_disabled()
    # Another customer claims a seat after the current UI loaded availability.
    with httpx.Client(base_url=address) as client:
        competitor = client.post(f'/events/{events[0]}/reservations',
            headers={'Authorization': 'Bearer '+create_access_token(users[1], SECRET), 'Idempotency-Key': 'browser-competitor'}, json={'seat_ids': [str(seats[1])]})
        assert competitor.status_code == 201
    page.get_by_role('button', name='Main, Row A, Seat 2: available', exact=True).click()
    page.locator('#reserve').click()
    expect(page.locator('#booking-status')).to_contain_text('already booked')
    expect(page.get_by_role('button', name='Main, Row A, Seat 2: unavailable', exact=True)).to_be_disabled()
    page.locator('#history-list').get_by_role('button', name='Cancel reservation').click()
    page.locator('#confirm-cancel').click()
    expect(page.locator('#history-list')).to_contain_text('Cancelled by you')
    expect(page.get_by_role('button', name='Main, Row A, Seat 1: available', exact=True)).to_be_enabled()
    page.get_by_role('button', name='Main, Row A, Seat 1: available', exact=True).click()
    page.locator('#reserve').click()
    expect(page.locator('#booking-status')).to_contain_text('Reservation confirmed')
    expect(page.locator('.reservation-card')).to_have_count(2)
    assert attempts[-1][0] != attempts[0][0]
    with httpx.Client(base_url=address) as client:
        assert client.post(f'/events/{events[0]}/cancel', headers={'Authorization': 'Bearer '+create_access_token(users[0], SECRET)}).status_code == 200
    page.locator('#history-refresh').click()
    expect(page.locator('#history-list')).to_contain_text('Cancelled by organizer')
    expect(page.locator('#history-list')).to_contain_text('Cancelled by you')
    page.get_by_role('button', name='View event & seats').click()
    expect(page.locator('#detail-copy')).to_contain_text('This event has been cancelled')
    expect(page.locator('#seat-status')).to_contain_text('0 of 21')
    page.set_viewport_size({'width': 375, 'height': 812})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_real_management_booking_and_event_cancellation(page, demo_url):
    """Three browser identities complete venue, hosting, booking, and cancellation flows."""
    from uuid import uuid4
    from concurrent.futures import ThreadPoolExecutor
    users, _, _, _, start = demo_url['data']
    customer = uuid4()
    # Record the extra fixture identity before insertion so failure cleanup includes it.
    users.append(customer)

    async def prepare_permissions():
        """Simulate operator promotion; public registration never grants management roles."""
        engine = create_async_engine(demo_url['database_url'])
        try:
            async with engine.begin() as conn:
                await conn.execute(update(User).where(User.id == users[0]).values(is_venue_manager=True))
                await conn.execute(update(User).where(User.id == users[1]).values(is_organizer=True))
                hashed = await conn.scalar(select(User.password_hash).where(User.id == users[0]))
                await conn.execute(User.__table__.insert().values(id=customer,
                    email=f'{customer}@example.com', password_hash=hashed))
        finally:
            await engine.dispose()

    def database_check(coroutine):
        """Run async DB work off the thread owning Playwright's synchronous event loop."""
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coroutine).result(timeout=10)

    database_check(prepare_permissions())
    page.goto(demo_url['url'] + '/app/')

    def login_as(identity):
        """Switch actual server-authenticated accounts without injecting browser tokens."""
        if page.locator('#logout').is_visible():
            page.locator('#logout').click()
        page.locator('#account').click()
        page.locator('#auth-email').fill(f'{identity}@example.com')
        page.locator('#auth-password').fill(demo_url['password'])
        page.locator('#auth-submit').click()
        expect(page.locator('#identity')).to_have_text(f'{identity}@example.com')

    hall = f'Management Hall {customer}'
    concert = f'Management Concert {customer}'
    login_as(users[0])
    page.locator('#venues-nav').click()
    page.locator('#venues-content [name=name]').fill(hall)
    page.locator('#venues-content [name=address]').fill('Browser Street')
    page.get_by_role('button', name='Create venue', exact=True).click()
    page.locator('#venues-content .event-card').filter(has_text=hall).get_by_role('button', name='Manage venue').click()
    page.locator('.seat-editor [name=section]').fill('Main')
    page.locator('.seat-editor [name=row]').fill('A')
    page.locator('.seat-editor [name=number]').fill('1')
    page.get_by_role('button', name='Add seats', exact=True).click()
    expect(page.locator('#venues-content')).to_contain_text('Seats added.')
    page.get_by_label('Organizer account UUID').fill(str(users[1]))
    page.get_by_role('button', name='Grant access', exact=True).click()
    expect(page.locator('#venue-access li')).to_contain_text(str(users[1]))

    login_as(users[1])
    page.locator('#organizer-nav').click()
    form = page.locator('#organizer-content form')
    form.locator('[name=name]').fill(concert)
    form.locator('[name=description]').fill('Browser-created description')
    form.locator('[name=venue_id]').select_option(label=hall + ' — Browser Street')
    form.locator('[name=starts_at]').fill(start.strftime('%Y-%m-%dT%H:%M'))
    with page.expect_response(lambda response: response.url.endswith('/events') and response.request.method == 'POST') as created:
        form.get_by_role('button', name='Create event', exact=True).click()
    assert created.value.status == 201
    event_id = created.value.json()['id']
    expect(page.locator('#organizer-content .event-card')).to_contain_text(concert)

    login_as(customer)
    page.locator('#filters [name=q]').fill(concert)
    page.get_by_role('button', name='Find events', exact=True).click()
    page.locator('#event-list .event-card').filter(has_text=concert).get_by_role('button', name='View event & seats').click()
    page.get_by_role('button', name='Main, Row A, Seat 1: available', exact=True).click()
    page.locator('#reserve').click()
    expect(page.locator('#history-list')).to_contain_text('Confirmed')

    login_as(users[1])
    page.locator('#organizer-nav').click()
    page.get_by_role('button', name='Manage event', exact=True).click()
    detail = page.locator('#organizer-content .detail')
    detail.locator('[name=name]').fill(concert + ' edited')
    detail.locator('[name=description]').fill('')
    detail.get_by_role('button', name='Save event details', exact=True).click()
    expect(page.locator('#organizer-content .event-card')).to_contain_text(concert + ' edited')
    page.get_by_role('button', name='Manage event', exact=True).click()
    page.once('dialog', lambda dialog: dialog.accept())
    detail.get_by_role('button', name='Cancel event', exact=True).click()
    expect(page.locator('#organizer-content .event-card .badge')).to_have_text('Cancelled')

    login_as(customer)
    expect(page.locator('#history-list')).to_contain_text('Cancelled by organizer')
    expect(page.locator('#history-list')).to_contain_text(concert + ' edited')
    with httpx.Client(base_url=demo_url['url']) as client:
        assert all(not seat['is_available'] for seat in client.get(f'/events/{event_id}/seats').json())

    async def verify_release():
        """Assert cancellation preserved history and physically released booking rows."""
        from uuid import UUID
        engine = create_async_engine(demo_url['database_url'])
        try:
            async with engine.connect() as conn:
                assert await conn.scalar(select(ReservationSeat.released_at).where(
                    ReservationSeat.event_id == UUID(event_id))) is not None
        finally:
            await engine.dispose()

    database_check(verify_release())
