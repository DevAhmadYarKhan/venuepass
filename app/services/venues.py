"""Venue persistence and public browsing operations."""

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.errors import VenueNotFound
from app.models import Venue
from app.schemas.venues import VenueCreate


async def create_venue(session: AsyncSession, payload: VenueCreate, *, owner_id: UUID) -> Venue:
    """Commit a venue with ownership provided by the authenticated caller."""
    venue = Venue(**payload.model_dump(), owner_id=owner_id)
    session.add(venue)
    await session.commit()
    await session.refresh(venue)
    return venue


async def list_venues(session: AsyncSession, *, limit: int, offset: int) -> list[Venue]:
    """Order by creation time and UUID for deterministic pages."""
    result = await session.scalars(
        select(Venue).order_by(Venue.created_at, Venue.id).limit(limit).offset(offset)
    )
    return list(result)


async def get_venue(session: AsyncSession, venue_id: UUID) -> Venue:
    """Load a venue or signal its absence independently of HTTP."""
    venue = await session.get(Venue, venue_id)
    if venue is None:
        raise VenueNotFound()
    return venue


async def lock_venue(session: AsyncSession, venue_id: UUID) -> Venue:
    """Serialize grants, seat additions, and event snapshots on the venue row."""
    venue = await session.scalar(select(Venue).where(Venue.id == venue_id).with_for_update())
    if venue is None:
        raise VenueNotFound()
    return venue
