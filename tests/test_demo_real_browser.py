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
from sqlalchemy import delete, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.models import Event, EventSeat, Reservation, ReservationSeat, Seat, User, Venue
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
                for model in (ReservationSeat, Reservation, EventSeat):
                    await conn.execute(delete(model).where(model.event_id.in_(events)))
                await conn.execute(delete(Event).where(Event.id.in_(events)))
                await conn.execute(delete(Seat).where(Seat.venue_id == venue))
                await conn.execute(delete(Venue).where(Venue.id == venue))
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
