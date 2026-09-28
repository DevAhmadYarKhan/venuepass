"""Owner-controlled organizer grants, serialized with event creation."""

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, VenueOrganizer
from app.errors import UserNotFound, OrganizerRequired, VenueOwnershipRequired
from app.services.venues import lock_venue, get_venue


async def change_access(session: AsyncSession, venue_id: UUID, organizer_id: UUID, owner_id: UUID, *, grant: bool) -> None:
    """Grant or revoke idempotently while holding the shared venue lock."""
    venue = await lock_venue(session, venue_id)
    if venue.owner_id != owner_id:
        raise VenueOwnershipRequired()
    user = await session.get(User, organizer_id)
    if user is None:
        raise UserNotFound()
    if grant and not user.is_organizer:
        raise OrganizerRequired()
    existing = await session.get(VenueOrganizer, (venue_id, organizer_id))
    if grant and existing is None:
        session.add(VenueOrganizer(venue_id=venue_id, organizer_id=organizer_id))
    elif not grant and existing is not None:
        await session.delete(existing)
    await session.commit()


async def list_organizers(session: AsyncSession, venue_id: UUID, owner_id: UUID, *, limit: int, offset: int) -> list[UUID]:
    """List explicit grants only; owners do not need an implicit grant record."""
    venue = await get_venue(session, venue_id)
    if venue.owner_id != owner_id:
        raise VenueOwnershipRequired()
    return list(await session.scalars(select(VenueOrganizer.organizer_id).where(
        VenueOrganizer.venue_id == venue_id
    ).order_by(VenueOrganizer.organizer_id).limit(limit).offset(offset)))
