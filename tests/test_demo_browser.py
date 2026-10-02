"""Exercise the real browser UI with deterministic HTTP responses and no database writes."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright, expect

# Browser installation is optional for ordinary API contributors; explicit runs
# enable this suite after `uv run playwright install chromium`.
pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.getenv('VENUEPASS_BROWSER_TESTS') != '1', reason='Set VENUEPASS_BROWSER_TESTS=1 to run Chromium tests')]


@pytest.fixture(scope='module')
def demo_url():
    """Start the actual application on a free loopback port without a reachable database."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    code = """
import uvicorn
from app.config import Settings
from app.main import create_app
app = create_app(Settings(_env_file=None, jwt_secret='test-signing-secret-that-is-at-least-32-characters', database_url='postgresql+psycopg://unused:unused@127.0.0.1:1/unavailable'))
uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='error')
""".replace('PORT', str(port))
    process = subprocess.Popen([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1])
    url = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urlopen(url+'/health', timeout=.2):
                    break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError('Demo server exited before startup')
                time.sleep(.05)
        else:
            raise RuntimeError('Demo server did not start')
        yield url
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.fixture
def page(demo_url):
    """Give each test a fresh isolated Chromium session and fail on JavaScript errors."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        yield page
        browser.close()
        assert not errors


def event(id='event-1', name='Evening concert'):
    """A stable public event response sufficient for all browsing views."""
    return {'id': id, 'name': name, 'description': '<img src=x onerror=alert(1)>',
            'venue_id': 'venue-1', 'starts_at': '2099-01-01T18:00:00Z',
            'ends_at': None, 'cancelled_at': None, 'capacity': 502,
            'organizer_id': 'owner', 'created_at': '2026-01-01T00:00:00Z'}


def fulfill(route, value, status=200):
    """Return API JSON without relying on demo fixtures in the development database."""
    route.fulfill(status=status, content_type='application/json', body=json.dumps(value))


def browsing_routes(page):
    """Mock API paths only; HTML, CSS, and modules are served by real FastAPI."""
    page.route('**/venues?*', lambda route: fulfill(route, [{'id': 'venue-1', 'name': 'The Green Hall'}]))
    page.route('**/events?*', lambda route: fulfill(route, [event()]))
    page.route('**/events/event-1', lambda route: fulfill(route, event()))


def test_filters_all_seat_pages_and_safe_content(page, demo_url):
    """Search sends absolute dates and seat browsing does not stop at the first page."""
    browsing_routes(page)
    seat_requests = []
    def seats(route):
        """Return 500 seats then two more, matching the API's bounded pagination."""
        seat_requests.append(route.request.url)
        start, end = (500, 502) if 'offset=500' in route.request.url else (0, 500)
        fulfill(route, [{'id': str(i), 'venue_id': 'venue-1', 'section': 'Main', 'row': 'A',
                         'number': i+1, 'is_available': i != 0} for i in range(start, end)])
    page.route('**/events/event-1/seats?*', seats)
    page.goto(demo_url+'/demo/')
    expect(page.get_by_role('heading', name='Evening concert', exact=True)).to_be_visible()
    page.get_by_label('Event name', exact=True).fill('Concert & talks')
    page.get_by_label('Starts from').fill('2099-01-01T10:00')
    with page.expect_request(lambda request: '/events?' in request.url and 'q=Concert' in request.url) as sent:
        page.get_by_role('button', name='Find events').click()
    assert 'starts_from=' in sent.value.url and 'upcoming_only=true' in sent.value.url
    page.get_by_role('button', name='View event & seats').click()
    expect(page.locator('#seat-status')).to_contain_text('501 of 502')
    assert len(seat_requests) == 2
    assert page.locator('#seat-list .seat').count() == 502
    expect(page.locator('#detail-copy')).to_contain_text('<img src=x onerror=alert(1)>')
    assert page.locator('#detail-copy img').count() == 0
    expect(page.locator('#detail-heading')).to_be_focused()
    page.get_by_role('button', name='Close details').click()
    expect(page.get_by_role('button', name='View event & seats')).to_be_focused()


def test_empty_failure_and_mobile_layout(page, demo_url):
    """Empty and network failure states are understandable and mobile pages do not overflow."""
    browsing_routes(page)
    page.route('**/events?*', lambda route: fulfill(route, []))
    page.set_viewport_size({'width': 375, 'height': 812})
    page.goto(demo_url+'/demo/')
    expect(page.locator('#browse-status')).to_contain_text('No events found')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.route('**/events?*', lambda route: route.abort())
    page.get_by_role('button', name='Find events').click()
    expect(page.locator('#browse-status')).to_contain_text('Cannot reach the server')
    page.get_by_label('Event name', exact=True).focus()
    page.keyboard.press('Tab')
    expect(page.get_by_role('combobox', name='Venue', exact=True)).to_be_focused()


def test_pagination_and_obsolete_filter_responses(page, demo_url):
    """Page controls use offsets and an older response cannot replace a newer search."""
    browsing_routes(page)
    pending = []
    def events(route):
        """Keep one search pending to deliberately deliver responses out of order."""
        url = route.request.url
        if 'q=slow' in url:
            pending.append(route)
        elif 'q=fast' in url:
            fulfill(route, [event(name='Fast result')])
        elif 'offset=6' in url:
            fulfill(route, [])
        else:
            fulfill(route, [event(id=f'event-{i}', name=f'Concert {i}') for i in range(6)])
    page.route('**/events?*', events)
    page.goto(demo_url+'/demo/')
    expect(page.locator('.event-card')).to_have_count(6)
    page.get_by_role('button', name='Next', exact=True).click()
    expect(page.locator('#page-label')).to_have_text('Page 2')
    expect(page.locator('#browse-status')).to_contain_text('No events found')
    page.get_by_role('button', name='Previous', exact=True).click()
    expect(page.locator('.event-card')).to_have_count(6)
    page.get_by_label('Event name', exact=True).fill('slow')
    page.get_by_role('button', name='Find events').click()
    page.wait_for_function("document.getElementById('browse-status').textContent.includes('Loading')")
    # Pump browser events until the route handler has actually received the request.
    deadline = time.monotonic() + 5
    while not pending and time.monotonic() < deadline:
        page.wait_for_timeout(10)
    assert pending
    page.get_by_label('Event name', exact=True).fill('fast')
    page.get_by_role('button', name='Find events').click()
    expect(page.get_by_role('heading', name='Fast result')).to_be_visible()
    pending[0].fulfill(status=200, content_type='application/json', body=json.dumps([event(name='Obsolete result')]))
    page.wait_for_timeout(100)
    expect(page.get_by_role('heading', name='Fast result')).to_be_visible()
    expect(page.get_by_role('heading', name='Obsolete result')).to_have_count(0)


def test_pagination_uses_applied_filters_and_venues_do_not_block(page, demo_url):
    """Unsubmitted form edits cannot skip results; optional venue data loads independently."""
    browsing_routes(page)
    venue_requests = []
    page.route('**/venues?*', lambda route: venue_requests.append(route))
    page.route('**/events?*', lambda route: fulfill(route, [event(id=f'event-{i}') for i in range(6)]))
    page.goto(demo_url+'/demo/', wait_until='domcontentloaded')
    expect(page.locator('.event-card')).to_have_count(6)
    page.get_by_label('Event name', exact=True).fill('not submitted')
    with page.expect_request(lambda request: '/events?' in request.url and 'offset=6' in request.url) as sent:
        page.get_by_role('button', name='Next', exact=True).click()
    assert 'q=' not in sent.value.url
    assert venue_requests
    fulfill(venue_requests[0], [{'id': 'venue-1', 'name': 'Late venue'}])
    expect(page.locator('.venue-name').first).to_have_text('Late venue')
    expect(page.locator('#page-label')).to_have_text('Page 2')


def auth_routes(page):
    """Provide a stable identity while retaining real browser credential submission."""
    page.route('**/users/me/reservations?*', lambda route: fulfill(route, []))
    page.route('**/auth/login', lambda route: fulfill(route, {'access_token': 'demo-token', 'expires_in': 1800, 'token_type': 'bearer'}))
    page.route('**/users/me', lambda route: fulfill(route, {'id': 'user-1', 'email': 'customer@example.com', 'is_organizer': False, 'is_venue_manager': False}))


def login(page):
    """Log in through labelled fields, not by injecting authentication state."""
    page.locator('#account').click()
    page.get_by_label('Email', exact=True).fill('customer@example.com')
    page.get_by_label('Password', exact=True).fill('a sufficiently long password')
    page.locator('#auth-submit').click()
    expect(page.locator('#identity')).to_have_text('customer@example.com')


def test_registration_login_logout_and_memory_only_session(page, demo_url):
    """Registration leads to login and neither storage nor refresh preserves the token."""
    browsing_routes(page)
    auth_routes(page)
    registrations = []
    def register(route):
        """Record only the transport shape, without retaining the password in test output."""
        registrations.append((route.request.method, route.request.url))
        fulfill(route, {'id': 'user-1', 'email': 'customer@example.com'}, 201)
    page.route('**/auth/register', register)
    page.goto(demo_url+'/demo/')
    page.locator('#account').click()
    page.locator('#register-mode').click()
    page.get_by_label('Email', exact=True).fill('customer@example.com')
    page.get_by_label('Password', exact=True).fill('a sufficiently long password')
    page.locator('#auth-submit').click()
    expect(page.locator('#auth-form-status')).to_contain_text('Account created. Log in')
    assert registrations == [('POST', demo_url+'/auth/register')]
    expect(page.locator('#auth-password')).to_have_value('')
    expect(page.locator('#identity')).to_have_text('')
    with page.expect_request(lambda request: request.url.endswith('/users/me')) as identity:
        page.get_by_label('Password', exact=True).fill('a sufficiently long password')
        page.locator('#auth-submit').click()
    expect(page.locator('#identity')).to_have_text('customer@example.com')
    assert identity.value.headers['authorization'] == 'Bearer demo-token'
    assert page.evaluate('localStorage.length + sessionStorage.length') == 0
    assert 'demo-token' not in page.url
    page.locator('#logout').click()
    expect(page.locator('#identity')).to_have_text('')
    login(page)
    page.reload()
    expect(page.locator('#account')).to_have_text('Log in / register')
    expect(page.locator('#identity')).to_have_text('')


def test_invalid_credentials_and_expired_session(page, demo_url):
    """Server errors clear passwords, and 401 prompts for a new login without stale identity."""
    browsing_routes(page)
    auth_routes(page)
    page.route('**/auth/login', lambda route: fulfill(route, {'detail': 'Invalid authentication credentials'}, 401))
    page.goto(demo_url+'/demo/')
    page.locator('#account').click()
    page.get_by_label('Email', exact=True).fill('customer@example.com')
    page.get_by_label('Password', exact=True).fill('wrong password')
    page.locator('#auth-submit').click()
    expect(page.locator('#auth-form-status')).to_contain_text('Invalid authentication credentials')
    expect(page.locator('#auth-password')).to_have_value('')
    auth_routes(page)
    page.get_by_label('Password', exact=True).fill('a sufficiently long password')
    page.locator('#auth-submit').click()
    expect(page.locator('#identity')).to_have_text('customer@example.com')
    page.route('**/private', lambda route: fulfill(route, {'detail': 'Invalid authentication credentials'}, 401))
    assert page.evaluate("async () => { const auth = await import('/demo/auth.js'); try { await auth.authRequest('/private'); } catch (e) { return e.status; } }") == 401
    expect(page.locator('#identity')).to_have_text('')
    expect(page.locator('#auth-status')).to_contain_text('session expired')
    expect(page.locator('#auth-dialog')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#auth-dialog')).not_to_be_visible()
