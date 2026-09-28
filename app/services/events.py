"""Event persistence and browsing, independent of HTTP routing."""

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import EventNotFound, EmptyVenue, VenueAccessDenied, OrganizerRequired
from app.models import Event, EventSeat, Seat, User, VenueOrganizer
from app.services.venues import lock_venue
from app.schemas.events import EventCreate


async def create_event(session: AsyncSession, payload: EventCreate, *, organizer_id: UUID) -> Event:
    """Commit validated event data and load generated fields."""
    venue = await lock_venue(session, payload.venue_id)
    organizer = await session.get(User, organizer_id, populate_existing=True)
    if organizer is None or not organizer.is_organizer:
        raise OrganizerRequired()
    grant = await session.get(VenueOrganizer, (venue.id, organizer_id))
    if venue.owner_id != organizer_id and grant is None:
        raise VenueAccessDenied()
    seat_ids = list(await session.scalars(select(Seat.id).where(Seat.venue_id == venue.id)))
    if not seat_ids:
        raise EmptyVenue()
    try:
        event = Event(**payload.model_dump(), organizer_id=organizer_id, capacity=len(seat_ids))
        session.add(event)
        await session.flush()
        # Membership and capacity are committed with the event, never separately.
        session.add_all([EventSeat(event_id=event.id, seat_id=id_, venue_id=venue.id) for id_ in seat_ids])
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(event)
    return event


async def list_events(session: AsyncSession, *, limit: int, offset: int) -> list[Event]:
    """Include past events and break equal-start-time ties with UUID ordering."""
    result = await session.scalars(
        select(Event).order_by(Event.starts_at, Event.id).limit(limit).offset(offset)
    )
    return list(result)


async def get_event(session: AsyncSession, event_id: UUID) -> Event:
    """Return stored event data or raise an application-level missing-event error."""
    event = await session.get(Event, event_id)
    if event is None:
        raise EventNotFound()
    return event


async def list_event_seats(session: AsyncSession, event_id: UUID, *, limit: int, offset: int) -> list[Seat]:
    """Read frozen membership rather than the venue's potentially expanded layout."""
    await get_event(session, event_id)
    result = await session.scalars(select(Seat).join(EventSeat, EventSeat.seat_id == Seat.id).where(
        EventSeat.event_id == event_id
    ).order_by(Seat.section, Seat.row, Seat.number).limit(limit).offset(offset))
    return list(result)
