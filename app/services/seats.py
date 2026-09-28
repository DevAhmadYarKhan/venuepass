"""Atomic seat creation and ordered venue seat queries."""

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.errors import DuplicateSeat, VenueOwnershipRequired
from app.models import Seat
from app.schemas.seats import SeatBatch
from app.services.venues import get_venue


async def create_seats(session: AsyncSession, venue_id: UUID, owner_id: UUID, payload: SeatBatch) -> list[Seat]:
    """Require ownership and commit every seat together or none at all."""
    venue = await get_venue(session, venue_id)
    if venue.owner_id != owner_id:
        raise VenueOwnershipRequired()
    identities = [(seat.section, seat.row, seat.number) for seat in payload.seats]
    if len(set(identities)) != len(identities):
        raise DuplicateSeat()
    seats = [Seat(venue_id=venue_id, **seat.model_dump()) for seat in payload.seats]
    try:
        # A common insertion order reduces deadlock risk for overlapping batches.
        # Keep the original list for the response's requested order.
        session.add_all(sorted(seats, key=lambda seat: (seat.section, seat.row, seat.number)))
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # PostgreSQL uniqueness arbitrates concurrent inserts, not a preflight lookup.
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "uq_seats_identity":
            raise DuplicateSeat() from exc
        raise
    return seats


async def list_seats(session: AsyncSession, venue_id: UUID, *, limit: int, offset: int) -> list[Seat]:
    """Distinguish a missing venue from an existing venue with no seats."""
    await get_venue(session, venue_id)
    result = await session.scalars(select(Seat).where(Seat.venue_id == venue_id).order_by(
        Seat.section, Seat.row, Seat.number
    ).limit(limit).offset(offset))
    return list(result)
