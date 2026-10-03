"""Event persistence and browsing, independent of HTTP routing."""

from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import EventNotFound, EmptyVenue, VenueAccessDenied, OrganizerRequired, EventOwnershipRequired, EventCancellationConflict, EventEditConflict, EventCreationConflict
from app.models import Event, EventSeat, Seat, User, VenueOrganizer, Reservation, ReservationSeat
from app.services.venues import lock_venue
from app.schemas.events import EventCreate, EventFilters, EventUpdate
from app.schemas.seats import EventSeatRead


async def create_event(session: AsyncSession, payload: EventCreate, *, organizer_id: UUID) -> Event:
    """Recheck creation eligibility after venue locking and commit fixed membership."""
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
        # Request validation precedes lock waits and queries; recheck the clock
        # immediately before insertion and roll back if the start time has passed.
        if payload.starts_at <= utc_now():
            raise EventCreationConflict("Event start time must be in the future")
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


def utc_now() -> datetime:
    """Supply an injectable wall-clock cutoff for creation and event discovery."""
    return datetime.now(timezone.utc)


async def list_events(session: AsyncSession, *, filters: EventFilters) -> list[Event]:
    """Combine filters before pagination and break equal start-time ties with UUIDs."""
    query = select(Event)
    if not filters.include_cancelled:
        query = query.where(Event.cancelled_at.is_(None))
    if filters.q is not None:
        # Escape LIKE metacharacters so user input is a literal substring, not a pattern.
        query = query.where(Event.name.icontains(filters.q, autoescape=True))
    if filters.venue_id is not None:
        query = query.where(Event.venue_id == filters.venue_id)
    if filters.starts_from is not None:
        query = query.where(Event.starts_at >= filters.starts_from)
    if filters.starts_before is not None:
        query = query.where(Event.starts_at < filters.starts_before)
    if filters.upcoming_only:
        # Capture the cutoff once; an event at this exact instant is already started.
        query = query.where(Event.starts_at > utc_now())
    result = await session.scalars(query.order_by(Event.starts_at, Event.id)
        .limit(filters.limit).offset(filters.offset))
    return list(result)


async def get_event(session: AsyncSession, event_id: UUID) -> Event:
    """Return stored event data or raise an application-level missing-event error."""
    event = await session.get(Event, event_id)
    if event is None:
        raise EventNotFound()
    return event


async def list_event_seats(session: AsyncSession, event_id: UUID, *, limit: int, offset: int) -> list[EventSeatRead]:
    """Read membership and booking state together; creation still rechecks availability."""
    event = await get_event(session, event_id)
    booked = select(ReservationSeat.seat_id).where(
        ReservationSeat.event_id == event_id, ReservationSeat.seat_id == Seat.id,
        ReservationSeat.released_at.is_(None)
    ).exists()
    rows = await session.execute(select(Seat, booked.label("booked")).join(EventSeat, EventSeat.seat_id == Seat.id).where(
        EventSeat.event_id == event_id
    ).order_by(Seat.section, Seat.row, Seat.number).limit(limit).offset(offset))
    upcoming = event.cancelled_at is None and event.starts_at > datetime.now(timezone.utc)
    return [EventSeatRead(id=seat.id, venue_id=seat.venue_id, section=seat.section,
        row=seat.row, number=seat.number, is_available=upcoming and not occupied)
        for seat, occupied in rows]


async def cancel_event(session: AsyncSession, event_id: UUID, *, organizer_id: UUID) -> Event:
    """Cancel the event and active bookings atomically while preserving prior history."""
    try:
        # Booking creation and both cancellation flows lock this same event row.
        # Ownership, current state, and the clock are checked after any lock wait.
        event = await session.scalar(select(Event).where(Event.id == event_id).with_for_update())
        if event is None:
            raise EventNotFound()
        organizer = await session.get(User, organizer_id, populate_existing=True)
        if organizer is None or not organizer.is_organizer:
            raise OrganizerRequired()
        if event.organizer_id != organizer_id:
            raise EventOwnershipRequired()
        if event.cancelled_at is None:
            now = utc_now()
            if event.starts_at <= now:
                raise EventCancellationConflict("Event has already started")
            event.cancelled_at = now
            # Bulk updates avoid loading every customer booking into memory. Never
            # overwrite an earlier customer's cancellation reason or timestamp.
            await session.execute(update(Reservation).where(
                Reservation.event_id == event_id, Reservation.cancelled_at.is_(None)
            ).values(cancelled_at=now, cancellation_reason="event_cancelled"))
            await session.execute(update(ReservationSeat).where(
                ReservationSeat.event_id == event_id, ReservationSeat.released_at.is_(None)
            ).values(released_at=now))
        await session.commit()
        await session.refresh(event)
        return event
    except Exception:
        await session.rollback()
        raise


async def list_organizer_events(session: AsyncSession, organizer_id: UUID, *, limit: int, offset: int) -> list[Event]:
    """Return owned events including historical ones, newest first with stable ties."""
    result = await session.scalars(select(Event).where(Event.organizer_id == organizer_id)
        .order_by(Event.created_at.desc(), Event.id.desc()).limit(limit).offset(offset))
    return list(result)


async def update_event(session: AsyncSession, event_id: UUID, payload: EventUpdate, *, organizer_id: UUID) -> Event:
    """Edit only descriptive fields after acquiring the event row used by cancellation."""
    try:
        event = await session.scalar(select(Event).where(Event.id == event_id).with_for_update())
        if event is None:
            raise EventNotFound()
        organizer = await session.get(User, organizer_id, populate_existing=True)
        if organizer is None or not organizer.is_organizer:
            raise OrganizerRequired()
        if event.organizer_id != organizer_id:
            raise EventOwnershipRequired()
        if event.cancelled_at is not None:
            raise EventEditConflict("Event has been cancelled")
        # Read wall-clock time after any lock wait, rather than before the transaction.
        if event.starts_at <= utc_now():
            raise EventEditConflict("Event has already started")
        # Explicit null clears the description; omitted fields never overwrite stored values.
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(event, field, value)
        await session.commit()
        await session.refresh(event)
        return event
    except Exception:
        await session.rollback()
        raise
