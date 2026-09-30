"""Atomic confirmed bookings with event-scoped serialization and retry recovery."""

from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.errors import BookingConflict, EventNotFound, InvalidReservationSeats, ReservationNotFound
from app.models import Event, EventSeat, Reservation, ReservationSeat
from app.schemas.reservations import ReservationRead


def utc_now() -> datetime:
    """Use wall-clock UTC after lock acquisition, not transaction-start time."""
    return datetime.now(timezone.utc)


async def serialize_many(session: AsyncSession, bookings: list[Reservation]) -> list[ReservationRead]:
    """Load seat lists in one query to avoid a query per booking in history."""
    if not bookings:
        return []
    seats = {booking.id: [] for booking in bookings}
    rows = await session.execute(select(ReservationSeat.reservation_id, ReservationSeat.seat_id).where(
        ReservationSeat.reservation_id.in_(seats)
    ).order_by(ReservationSeat.seat_id))
    for reservation_id, seat_id in rows:
        seats[reservation_id].append(seat_id)
    return [ReservationRead(id=b.id, event_id=b.event_id, user_id=b.user_id,
        seat_ids=seats[b.id], created_at=b.created_at) for b in bookings]


async def create_reservation(session: AsyncSession, event_id: UUID, user_id: UUID, seat_ids: list[UUID], key: str) -> ReservationRead:
    """Commit the booking, all seats, and retry identity as a single unit."""
    try:
        event = await session.scalar(select(Event).where(Event.id == event_id).with_for_update())
        if event is None:
            raise EventNotFound()
        # Check retries after waiting for earlier requests, before time/availability checks.
        existing = await session.scalar(select(Reservation).where(
            Reservation.user_id == user_id, Reservation.event_id == event_id,
            Reservation.idempotency_key == key,
        ))
        if existing is not None:
            result = (await serialize_many(session, [existing]))[0]
            if set(result.seat_ids) != set(seat_ids):
                raise BookingConflict("Idempotency key already used for different seats")
            await session.commit()
            return result
        if event.starts_at <= utc_now():
            raise BookingConflict("Event has already started")
        members = set(await session.scalars(select(EventSeat.seat_id).where(
            EventSeat.event_id == event_id, EventSeat.seat_id.in_(seat_ids)
        )))
        if members != set(seat_ids):
            raise InvalidReservationSeats()
        booked = await session.scalar(select(ReservationSeat.seat_id).where(
            ReservationSeat.event_id == event_id, ReservationSeat.seat_id.in_(seat_ids)
        ).limit(1))
        if booked is not None:
            raise BookingConflict("One or more seats are already booked")
        booking = Reservation(event_id=event_id, user_id=user_id, idempotency_key=key)
        session.add(booking)
        await session.flush()
        session.add_all([ReservationSeat(reservation_id=booking.id, event_id=event_id, seat_id=id_) for id_ in sorted(seat_ids)])
        await session.flush()
        result = (await serialize_many(session, [booking]))[0]
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "uq_reservation_seats_event_seat":
            raise BookingConflict("One or more seats are already booked") from exc
        raise
    except Exception:
        await session.rollback()
        raise


async def get_reservation(session: AsyncSession, reservation_id: UUID, user_id: UUID) -> ReservationRead:
    """Filter by owner so other users' IDs behave exactly like missing IDs."""
    booking = await session.scalar(select(Reservation).where(Reservation.id == reservation_id, Reservation.user_id == user_id))
    if booking is None:
        raise ReservationNotFound()
    return (await serialize_many(session, [booking]))[0]


async def list_reservations(session: AsyncSession, user_id: UUID, *, limit: int, offset: int) -> list[ReservationRead]:
    """Return the current user's bookings newest first, with a stable UUID tie-break."""
    bookings = list(await session.scalars(select(Reservation).where(Reservation.user_id == user_id).order_by(
        Reservation.created_at.desc(), Reservation.id.desc()
    ).limit(limit).offset(offset)))
    return await serialize_many(session, bookings)
