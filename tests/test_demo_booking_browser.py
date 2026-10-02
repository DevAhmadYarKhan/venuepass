"""Browser booking workflows exercise retry identity, account isolation, and seat conflicts."""

from playwright.sync_api import expect
import pytest

# Reuse the actual server/browser fixtures; only HTTP API behavior is mocked here.
from test_demo_browser import auth_routes, browsing_routes, demo_url, event, fulfill, login, page

# Enable this module under the same opt-in condition as the shared browser fixture.
import os
pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.getenv('VENUEPASS_BROWSER_TESTS') != '1', reason='Set VENUEPASS_BROWSER_TESTS=1 to run Chromium tests')]


def seats(count=3, available=True):
    """Use physical labels independently of UUIDs to check customer-facing summaries."""
    return [{'id': f'seat-{i}', 'venue_id': 'venue-1', 'section': 'Main', 'row': 'A',
             'number': i+1, 'is_available': available} for i in range(count)]


def booking(id='reservation-1', cancelled=False, reason='customer'):
    """A complete reservation response used by creation, history, and retries."""
    return {'id': id, 'event_id': 'event-1', 'user_id': 'user-1', 'seat_ids': ['seat-0'],
            'created_at': '2026-01-01T00:00:00Z',
            'cancelled_at': '2026-01-02T00:00:00Z' if cancelled else None,
            'cancellation_reason': reason if cancelled else None}


def setup(page, demo_url, count=3):
    """Open real modules and authenticate through the UI before selecting seats."""
    browsing_routes(page)
    auth_routes(page)
    page.route('**/events/event-1/seats?*', lambda route: fulfill(route, seats(count)))
    page.goto(demo_url+'/demo/')
    login(page)
    page.get_by_role('button', name='View event & seats').click()
    expect(page.locator('#seat-status')).to_contain_text(f'{count} of {count}')


def test_booking_then_cancellation_and_history_labels(page, demo_url):
    """Successful booking refreshes availability and cancelling retains human-readable history."""
    setup(page, demo_url)
    history = []
    page.route('**/users/me/reservations?*', lambda route: fulfill(route, history))
    def reserve(route):
        """A real browser POST must carry a key, owner token, and explicit seats."""
        assert route.request.headers['authorization'] == 'Bearer demo-token'
        assert route.request.headers['idempotency-key']
        assert route.request.post_data_json == {'seat_ids': ['seat-0']}
        history.append(booking())
        fulfill(route, history[0], 201)
    page.route('**/events/event-1/reservations', reserve)
    page.get_by_role('button', name='Main, Row A, Seat 1: available', exact=True).click()
    expect(page.locator('#selection')).to_contain_text('Main · Row A · Seat 1')
    page.locator('#reserve').click()
    expect(page.locator('#booking-status')).to_contain_text('Reservation confirmed')
    expect(page.locator('#history-list')).to_contain_text('Evening concert')
    expect(page.locator('#history-list')).to_contain_text('Main · Row A · Seat 1')
    def cancel(route):
        """Cancellation needs neither a body nor a new idempotency key."""
        assert route.request.method == 'POST'
        assert 'idempotency-key' not in route.request.headers
        history[0] = booking(cancelled=True)
        fulfill(route, history[0])
    page.route('**/reservations/reservation-1/cancel', cancel)
    page.locator('#history-list').get_by_role('button', name='Cancel reservation').click()
    expect(page.locator('#cancel-dialog')).to_be_visible()
    page.locator('#confirm-cancel').click()
    expect(page.locator('#history-list')).to_contain_text('Cancelled by you')
    expect(page.locator('#cancel-dialog')).not_to_be_visible()


def test_uncertain_booking_reuses_exact_key_and_seats(page, demo_url):
    """A dropped response freezes intent; recovery must not create a second reservation."""
    setup(page, demo_url)
    attempts = []
    def reserve(route):
        """Simulate a committed booking whose first HTTP response is lost."""
        attempts.append((route.request.headers['idempotency-key'], route.request.post_data_json))
        if len(attempts) == 1:
            route.abort()
        else:
            fulfill(route, booking(), 201)
    page.route('**/events/event-1/reservations', reserve)
    page.get_by_role('button', name='Main, Row A, Seat 1: available', exact=True).click()
    page.locator('#reserve').click()
    expect(page.locator('#booking-status')).to_contain_text('outcome is uncertain')
    expect(page.locator('#reserve')).to_be_disabled()
    expect(page.get_by_role('button', name='Main, Row A, Seat 2: available', exact=True)).to_be_disabled()
    page.locator('#retry-booking').click()
    expect(page.locator('#booking-status')).to_contain_text('Reservation confirmed')
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    expect(page.locator('#pending-booking')).not_to_be_visible()


def test_conflict_refreshes_availability_and_new_intent_gets_new_key(page, demo_url):
    """A definitive seat conflict ends the failed attempt and permits a new selection."""
    setup(page, demo_url)
    attempts = []
    def reserve(route):
        """First attempt loses a seat race; the second is a different intended booking."""
        attempts.append((route.request.headers['idempotency-key'], route.request.post_data_json))
        fulfill(route, {'detail': 'One or more seats are already booked'}, 409) if len(attempts) == 1 else fulfill(route, booking(), 201)
    page.route('**/events/event-1/reservations', reserve)
    page.get_by_role('button', name='Main, Row A, Seat 1: available', exact=True).click()
    page.locator('#reserve').click()
    expect(page.locator('#booking-status')).to_contain_text('already booked')
    expect(page.locator('#selection-count')).to_contain_text('0 of 20')
    expect(page.locator('#pending-booking')).not_to_be_visible()
    page.get_by_role('button', name='Main, Row A, Seat 2: available', exact=True).click()
    page.locator('#reserve').click()
    expect(page.locator('#booking-status')).to_contain_text('Reservation confirmed')
    assert len(attempts) == 2 and attempts[0][0] != attempts[1][0]
    assert attempts[1][1] == {'seat_ids': ['seat-1']}


def test_expired_session_preserves_pending_booking_across_login(page, demo_url):
    """Reauthentication continues the original intent rather than generating another key."""
    setup(page, demo_url)
    attempts = []
    def reserve(route):
        """Reject an expired token, then recover the original request after login."""
        attempts.append((route.request.headers['idempotency-key'], route.request.post_data_json))
        if len(attempts) == 1: fulfill(route, {'detail': 'Invalid authentication credentials'}, 401)
        else: fulfill(route, booking(cancelled=True, reason='event_cancelled'), 201)
    page.route('**/events/event-1/reservations', reserve)
    page.get_by_role('button', name='Main, Row A, Seat 1: available', exact=True).click()
    page.locator('#reserve').click()
    expect(page.locator('#auth-dialog')).to_be_visible()
    page.get_by_label('Password', exact=True).fill('a sufficiently long password')
    page.locator('#auth-submit').click()
    expect(page.locator('#identity')).to_have_text('customer@example.com')
    page.locator('#retry-booking').click()
    expect(page.locator('#booking-status')).to_contain_text('original reservation is cancelled')
    assert attempts[0] == attempts[1]


def test_selection_limit_and_organizer_cancellation_history(page, demo_url):
    """A customer cannot select more than 20 seats; event cancellation is explained clearly."""
    setup(page, demo_url, count=21)
    for i in range(1, 22):
        page.get_by_role('button', name=f'Main, Row A, Seat {i}: available', exact=True).click()
    expect(page.locator('#selection-count')).to_contain_text('20 of 20')
    expect(page.locator('#booking-status')).to_contain_text('at most 20')
    page.route('**/users/me/reservations?*', lambda route: fulfill(route, [booking(cancelled=True, reason='event_cancelled')]))
    page.locator('#history-refresh').click()
    expect(page.locator('#history-list')).to_contain_text('Cancelled by organizer')
    page.locator('#logout').click()
    expect(page.locator('#history-list')).to_be_empty()
    expect(page.locator('#reserve')).to_be_disabled()


def test_reservation_pagination_and_uncertain_cancellation(page, demo_url):
    """Private pagination uses the requested offset and cancellation retry targets the same ID."""
    setup(page, demo_url)
    def history(route):
        """Provide exactly one full page followed by an empty page."""
        fulfill(route, [] if 'offset=6' in route.request.url else [booking(id=f'reservation-{i}') for i in range(6)])
    page.route('**/users/me/reservations?*', history)
    page.locator('#history-refresh').click()
    expect(page.locator('.reservation-card')).to_have_count(6)
    page.locator('#history-next').click()
    expect(page.locator('#history-page')).to_have_text('Page 2')
    expect(page.locator('#history-status')).to_contain_text('No reservations')
    page.locator('#history-previous').click()
    expect(page.locator('.reservation-card')).to_have_count(6)
    attempts = []
    def cancel(route):
        """Drop the first response and allow the naturally repeatable cancellation retry."""
        attempts.append(route.request.url)
        if len(attempts) == 1: route.abort()
        else: fulfill(route, booking(id='reservation-0', cancelled=True))
    page.route('**/reservations/reservation-0/cancel', cancel)
    page.locator('.reservation-card').first.get_by_role('button', name='Cancel reservation').click()
    page.locator('#confirm-cancel').click()
    expect(page.locator('#cancel-status')).to_contain_text('outcome is uncertain')
    page.locator('#confirm-cancel').click()
    expect(page.locator('#cancel-dialog')).not_to_be_visible()
    assert len(attempts) == 2 and attempts[0] == attempts[1]


def test_unresolved_booking_cannot_be_retried_as_another_account(page, demo_url):
    """User-scoped keys cannot be transferred to another account after logout."""
    setup(page, demo_url)
    attempts = []
    def reserve(route):
        """Leave one request uncertain, without allowing an automatic second POST."""
        attempts.append(route.request.headers['idempotency-key'])
        route.abort()
    page.route('**/events/event-1/reservations', reserve)
    page.get_by_role('button', name='Main, Row A, Seat 1: available', exact=True).click()
    page.locator('#reserve').click()
    expect(page.locator('#booking-status')).to_contain_text('outcome is uncertain')
    page.locator('#logout').click()
    page.route('**/users/me', lambda route: fulfill(route, {'id': 'user-2', 'email': 'other@example.com'}))
    page.locator('#account').click()
    page.get_by_label('Email', exact=True).fill('other@example.com')
    page.get_by_label('Password', exact=True).fill('a sufficiently long password')
    page.locator('#auth-submit').click()
    expect(page.locator('#identity')).to_have_text('other@example.com')
    page.locator('#retry-booking').click()
    expect(page.locator('#booking-status')).to_contain_text('log in as customer@example.com')
    assert len(attempts) == 1
    expect(page.locator('#reserve')).to_be_disabled()


def test_logout_discards_late_private_history_and_slow_metadata_does_not_block(page, demo_url):
    """History renders without enrichment and a late private response cannot reappear after logout."""
    setup(page, demo_url)
    page.route('**/users/me/reservations?*', lambda route: fulfill(route, [booking()]))
    metadata_requests = []
    page.route('**/events/event-1', lambda route: metadata_requests.append(route))
    page.locator('#history-refresh').click()
    expect(page.locator('.reservation-card')).to_have_count(1)
    expect(page.locator('#history-list')).to_contain_text('Event event-1')
    expect(page.locator('#history-list').get_by_role('button', name='Cancel reservation')).to_be_visible()
    pending_history = []
    page.route('**/users/me/reservations?*', lambda route: pending_history.append(route))
    page.locator('#history-refresh').click()
    expect(page.locator('#history-status')).to_contain_text('Loading reservations')
    # Playwright's event pumping waits until the HTTP request has reached its handler.
    page.wait_for_timeout(100)
    assert pending_history
    page.locator('#logout').click()
    fulfill(pending_history[0], [booking()])
    for route in metadata_requests:
        fulfill(route, event())
    page.wait_for_timeout(100)
    expect(page.locator('#history-list')).to_be_empty()
    expect(page.locator('#history-status')).to_contain_text('Log in')
