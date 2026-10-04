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
    page.route('**/users/me/events?*', lambda route: fulfill(route, []))
    page.route('**/users/me/hosting-venues?*', lambda route: fulfill(route, []))
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
    page.route('**/venues/venue-created/organizers?*', lambda route: fulfill(route, []))
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


def test_organizer_access_management(page, demo_url):
    """Owner grants and confirmed revocations accept empty successful responses."""
    browsing_routes(page)
    page.route('**/users/me/reservations?*', lambda route: fulfill(route, []))
    page.route('**/users/me/venues?*', lambda route: fulfill(route, [
        {'id': 'venue-access', 'name': 'Access Hall', 'address': 'Street', 'owner_id': 'owner-id'}]))
    page.route('**/venues/venue-access/seats?*', lambda route: fulfill(route, []))
    identities = []
    identity = '00000000-0000-0000-0000-000000000002'
    page.route('**/venues/venue-access/organizers?*', lambda route: fulfill(route, identities))

    def change(route):
        """Record exactly one explicit grant and model a naturally repeatable revoke."""
        if route.request.method == 'PUT':
            if identity not in identities:
                identities.append(identity)
        else:
            identities.clear()
        route.fulfill(status=204, body='')

    page.route(f'**/venues/venue-access/organizers/{identity}', change)
    page.goto(demo_url + '/app/')
    login(page)
    page.locator('#venues-nav').click()
    page.get_by_role('button', name='Manage venue').click()
    expect(page.locator('#venue-access')).to_contain_text('No explicit organizer grants')
    for _ in range(2):
        page.get_by_label('Organizer account UUID').fill(identity)
        page.get_by_role('button', name='Grant access', exact=True).click()
        expect(page.locator('#venue-access li')).to_have_count(1)
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='Revoke access', exact=True).click()
    expect(page.locator('#venue-access')).to_contain_text('No explicit organizer grants')
    failed_identity = '00000000-0000-0000-0000-000000000003'
    def rejection(code, detail):
        """Bind expected errors without optional callback parameters Playwright fills."""
        def respond(route):
            """Return the configured application failure for this grant attempt."""
            fulfill(route, {'detail': detail}, code)
        return respond
    for code, detail in [(404, 'User not found'), (409, 'User is not an organizer'), (403, 'Venue ownership required')]:
        page.route(f'**/venues/venue-access/organizers/{failed_identity}',
            rejection(code, detail))
        page.get_by_label('Organizer account UUID').fill(failed_identity)
        page.get_by_role('button', name='Grant access', exact=True).click()
        expect(page.locator('#venue-access')).to_contain_text(detail)
        expect(page.get_by_role('button', name='Grant access', exact=True)).to_be_enabled()
        expect(page.get_by_label('Organizer account UUID')).to_have_value(failed_identity)
    # A mutation response delivered after logout cannot refresh a private view.
    pending = []
    page.route(f'**/venues/venue-access/organizers/{identity}', lambda route: pending.append(route))
    page.get_by_label('Organizer account UUID').fill(identity)
    with page.expect_request(f'**/venues/venue-access/organizers/{identity}'):
        page.get_by_role('button', name='Grant access', exact=True).click()
    page.locator('#logout').click()
    pending[0].fulfill(status=204, body='')
    expect(page.locator('#venues-view')).not_to_be_visible()
    expect(page.locator('#venues-content')).not_to_contain_text(identity)


@pytest.mark.parametrize('failure_status', [403, 409, 422, 500])
def test_organizer_event_creation(page, demo_url, failure_status):
    """Create with authorized choices, validate local times, and preserve failed input."""
    browsing_routes(page)
    page.route('**/users/me/reservations?*', lambda route: fulfill(route, []))
    owned = []
    attempts = []
    page.route('**/users/me/events?*', lambda route: fulfill(route, owned))
    page.route('**/users/me/hosting-venues?*', lambda route: fulfill(route, [
        {'id': 'venue-1', 'name': 'Authorized Hall', 'address': 'Street'}]))

    def create(route):
        """Model one failed transaction followed by an explicit successful submission."""
        payload = route.request.post_data_json
        attempts.append(payload)
        if len(attempts) == 1:
            fulfill(route, {'detail': 'Creation refused'}, failure_status)
        else:
            result = {**event(), **payload, 'organizer_id': 'owner-id', 'cancelled_at': None}
            owned.append(result)
            fulfill(route, result, 201)

    page.route('**/events', create)
    page.goto(demo_url + '/app/')
    login(page)
    page.locator('#organizer-nav').click()
    form = page.locator('#organizer-content form')
    form.locator('[name=name]').fill('Created concert')
    form.locator('[name=description]').fill('A new concert')
    form.locator('[name=venue_id]').select_option('venue-1')
    form.locator('[name=starts_at]').fill('2000-01-01T18:00')
    form.get_by_role('button', name='Create event', exact=True).click()
    expect(form).to_contain_text('Choose a future start')
    assert attempts == []
    form.locator('[name=starts_at]').fill('2099-01-01T18:00')
    form.locator('[name=ends_at]').fill('2099-01-01T19:00')
    form.get_by_role('button', name='Create event', exact=True).click()
    expect(form).to_contain_text('Creation refused')
    expect(form.locator('[name=name]')).to_have_value('Created concert')
    if failure_status == 500:
        expect(form).to_contain_text('outcome is uncertain')
    assert len(attempts) == 1
    form.get_by_role('button', name='Create event', exact=True).click()
    expect(page.locator('#organizer-content .event-card')).to_contain_text('Created concert')
    assert attempts[-1]['starts_at'].endswith('Z')
    assert attempts[-1]['ends_at'].endswith('Z')


def test_organizer_without_hosting_access(page, demo_url):
    """No granted venues produces an explanation rather than an unusable submission."""
    browsing_routes(page)
    page.route('**/users/me/reservations?*', lambda route: fulfill(route, []))
    page.route('**/users/me/events?*', lambda route: fulfill(route, []))
    page.route('**/users/me/hosting-venues?*', lambda route: fulfill(route, []))
    page.goto(demo_url + '/app/')
    login(page, manager=False)
    page.locator('#organizer-nav').click()
    expect(page.locator('#organizer-content')).to_contain_text('No authorized venues')
    expect(page.get_by_role('button', name='Create event', exact=True)).to_be_disabled()
