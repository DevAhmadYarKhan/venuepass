"""Verify private venue discovery follows current ownership, grants, and permissions."""

from uuid import uuid4
import pytest
from sqlalchemy import delete, update
from app.models import User, Venue, VenueOrganizer
from app.security import create_access_token

pytestmark = pytest.mark.integration
SECRET = 'test-signing-secret-that-is-at-least-32-characters'


async def test_private_venue_queries(event_client):
    """Filter before paging, avoid duplicate owned grants, and observe revocation."""
    client, conn = event_client
    owner, other = uuid4(), uuid4()
    own, granted, hidden = uuid4(), uuid4(), uuid4()
    for user in (owner, other):
        await conn.execute(User.__table__.insert().values(id=user,
            email=f'{user}@example.com', password_hash='unused',
            is_organizer=True, is_venue_manager=True))
    for venue, user in ((own, owner), (granted, other), (hidden, other)):
        await conn.execute(Venue.__table__.insert().values(id=venue, owner_id=user,
            name=str(venue), address='Street'))
    for venue in (own, granted):
        await conn.execute(VenueOrganizer.__table__.insert().values(
            venue_id=venue, organizer_id=owner))
    paths = ['/users/me/venues', '/users/me/hosting-venues']
    for path in paths:
        assert (await client.get(path)).status_code == 401
    client.headers['Authorization'] = 'Bearer ' + create_access_token(owner, SECRET)
    assert [v['id'] for v in (await client.get(paths[0])).json()] == [str(own)]
    rows = (await client.get(paths[1])).json()
    assert len(rows) == 2 and {v['id'] for v in rows} == {str(own), str(granted)}
    for offset, row in enumerate(rows):
        assert (await client.get(paths[1] + f'?limit=1&offset={offset}')).json() == [row]
    for path in paths:
        assert (await client.get(path + '?limit=101')).status_code == 422
        assert (await client.get(path + '?offset=-1')).status_code == 422
        assert (await client.get(path + '?offset=20')).json() == []
    await conn.execute(delete(VenueOrganizer).where(VenueOrganizer.venue_id == granted))
    assert [v['id'] for v in (await client.get(paths[1])).json()] == [str(own)]
    # Owners need no explicit grant; permission revocation still denies discovery.
    await conn.execute(delete(VenueOrganizer).where(VenueOrganizer.venue_id == own))
    assert [v['id'] for v in (await client.get(paths[1])).json()] == [str(own)]
    await conn.execute(update(User).where(User.id == owner).values(
        is_organizer=False, is_venue_manager=False))
    for path in paths:
        assert (await client.get(path)).status_code == 403
    client.headers['Authorization'] = 'Bearer ' + create_access_token(other, SECRET)
    assert {v['id'] for v in (await client.get(paths[0])).json()} == {str(granted), str(hidden)}
