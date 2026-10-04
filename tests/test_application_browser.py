"""Verify application navigation and shared transport behavior in real Chromium."""

import os
import pytest
from playwright.sync_api import expect
from test_demo_browser import page, demo_url, browsing_routes, fulfill, event

pytestmark = [pytest.mark.browser, pytest.mark.skipif(
    os.getenv('VENUEPASS_BROWSER_TESTS') != '1', reason='Enable browser tests')]


def login(page, *, organizer=True, manager=True):
    """Authenticate a mocked identity through the actual browser login form."""
    page.route('**/auth/login', lambda route: fulfill(route, {'access_token': 'test-token'}))
    page.route('**/users/me', lambda route: fulfill(route, {
        'id': 'owner-id', 'email': 'owner@example.com',
        'is_organizer': organizer, 'is_venue_manager': manager}))
    page.locator('#account').click()
    page.locator('#auth-email').fill('owner@example.com')
    page.locator('#auth-password').fill('long-enough-password')
    page.locator('#auth-submit').click()
    expect(page.locator('#auth-dialog')).not_to_be_visible()


def test_navigation_permissions_and_transport(page, demo_url):
    """Role views clear at logout; grants and rate-limit responses decode correctly."""
    browsing_routes(page)
    page.route('**/users/me/reservations?*', lambda route: fulfill(route, []))
    page.goto(demo_url + '/app/')
    expect(page.locator('#organizer-nav')).not_to_be_visible()
    login(page)
    expect(page.locator('#auth-status')).to_contain_text('owner-id')
    page.locator('#organizer-nav').click()
    expect(page.locator('#organizer-view')).to_be_visible()
    page.locator('#logout').click()
    expect(page.locator('#organizer-view')).not_to_be_visible()
    expect(page.locator('#organizer-nav')).not_to_be_visible()
    page.route('**/test-empty', lambda route: route.fulfill(status=204, body=''))
    assert page.evaluate("async () => (await import('/app/api.js')).request('/test-empty')") is None
    page.route('**/test-limit', lambda route: route.fulfill(status=429,
        content_type='application/json', headers={'Retry-After': '12'}, body='{"detail":"Too many requests"}'))
    message = page.evaluate("async () => { try { await (await import('/app/api.js')).request('/test-limit'); } catch(e) { return e.message; } }")
    assert '12 seconds' in message


def test_event_deep_link(page, demo_url):
    """A directly opened event URL loads details without a prior card selection."""
    identity = '00000000-0000-0000-0000-000000000001'
    page.route('**/venues?*', lambda route: fulfill(route, []))
    page.route('**/events?*', lambda route: fulfill(route, []))
    page.route(f'**/events/{identity}', lambda route: fulfill(route, event(id=identity)))
    page.route(f'**/events/{identity}/seats?*', lambda route: fulfill(route, []))
    page.goto(demo_url + '/app/#/events/' + identity)
    expect(page.locator('#detail-heading')).to_have_text('Evening concert')
    expect(page.locator('#event-detail')).to_be_visible()


@pytest.mark.parametrize('failure_status', [409, 403, 500])
def test_venue_and_seat_management(page, demo_url, failure_status):
    """Create venues/seats and explain duplicate, permission, and uncertain failures."""
    browsing_routes(page)
    page.route('**/users/me/reservations?*', lambda route: fulfill(route, []))
    venues = []
    seats = []

    def create_venue(route):
        """Return server-owned identity for the submitted venue details."""
        venue = {**route.request.post_data_json, 'id': 'venue-created', 'owner_id': 'owner-id'}
        venues.append(venue)
        fulfill(route, venue, 201)

    def create_seats(route):
        """Simulate an atomic duplicate check without writing a real database."""
        if seats:
            fulfill(route, {'detail': 'Duplicate seat' if failure_status == 409 else 'Seat request failed'}, failure_status)
        else:
            seats.extend([{**seat, 'id': 'seat-created', 'venue_id': 'venue-created'}
                for seat in route.request.post_data_json['seats']])
            fulfill(route, seats, 201)

    page.route('**/users/me/venues?*', lambda route: fulfill(route, venues))
    page.route('**/venues', create_venue)
    page.route('**/venues/venue-created/seats?*', lambda route: fulfill(route, seats))
    page.route('**/venues/venue-created/seats', create_seats)
    page.goto(demo_url + '/app/')
    login(page)
    page.locator('#venues-nav').click()
    page.locator('#venues-content input[name=name]').fill('New Hall')
    page.locator('#venues-content input[name=address]').fill('Main Street')
    page.get_by_role('button', name='Create venue', exact=True).click()
    expect(page.locator('#venues-content .event-card')).to_contain_text('New Hall')
    page.get_by_role('button', name='Manage venue').click()
    for _ in range(2):
        page.locator('.seat-editor input[name=section]').fill('Main')
        page.locator('.seat-editor input[name=row]').fill('A')
        page.locator('.seat-editor input[name=number]').fill('1')
        page.get_by_role('button', name='Add seats', exact=True).click()
        if not _:
            expect(page.locator('#venues-content')).to_contain_text('Seat 1')
            expect(page.locator('#venues-content')).to_contain_text('Seats added.')
    expect(page.locator('#venues-content')).to_contain_text('Duplicate seat' if failure_status == 409 else 'Seat request failed')
    if failure_status == 500:
        expect(page.locator('#venues-content')).to_contain_text('outcome is uncertain')
    page.set_viewport_size({'width': 375, 'height': 812})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
