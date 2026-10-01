"""Persisted event data and database-level invariants."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Event(Base):
    """An event whose capacity will support future ticket reservations."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("id", "venue_id", name="uq_events_id_venue"),
        CheckConstraint("capacity > 0", name="ck_events_capacity_positive"),
        CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at", name="ck_events_end_after_start"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Ownership is assigned from the authenticated organizer, never client input.
    organizer_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    venue_id: Mapped[UUID] = mapped_column(ForeignKey("venues.id", ondelete="RESTRICT"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Cancellation retains the event and its immutable booking history.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int] = mapped_column(Integer)
    # PostgreSQL supplies creation time consistently for every database writer.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class User(Base):
    """Local account with a unique normalized email and an Argon2 password hash."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(Text)
    # Registration cannot opt into this permission; only local management grants it.
    is_organizer: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Venue management is independent of event organization.
    is_venue_manager: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Venue(Base):
    """A physical location owned by the manager who created it."""

    __tablename__ = "venues"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(1000))
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Seat(Base):
    """A physical venue seat; booking availability belongs to future event bookings."""

    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint("id", "venue_id", name="uq_seats_id_venue"),
        UniqueConstraint("venue_id", "section", "row", "number", name="uq_seats_identity"),
        CheckConstraint("number > 0", name="ck_seats_number_positive"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    venue_id: Mapped[UUID] = mapped_column(ForeignKey("venues.id", ondelete="RESTRICT"))
    section: Mapped[str] = mapped_column(String(100))
    row: Mapped[str] = mapped_column(String(100))
    number: Mapped[int] = mapped_column(Integer)


class VenueOrganizer(Base):
    """An owner's continuing grant for an organizer to create events at a venue."""

    __tablename__ = "venue_organizers"
    venue_id: Mapped[UUID] = mapped_column(ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True)
    organizer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class EventSeat(Base):
    """Frozen membership; composite keys ensure event and seat share a venue."""

    __tablename__ = "event_seats"
    __table_args__ = (
        ForeignKeyConstraint(["event_id", "venue_id"], ["events.id", "events.venue_id"], ondelete="RESTRICT", name="fk_event_seats_event_venue"),
        ForeignKeyConstraint(["seat_id", "venue_id"], ["seats.id", "seats.venue_id"], ondelete="RESTRICT", name="fk_event_seats_seat_venue"),
    )
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    seat_id: Mapped[UUID] = mapped_column(primary_key=True)
    venue_id: Mapped[UUID] = mapped_column(nullable=False)


class Reservation(Base):
    """Confirmed booking and its successful retry identity, committed together."""

    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint(
            "(cancelled_at IS NULL AND cancellation_reason IS NULL) OR "
            "(cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL AND "
            "cancellation_reason IN ('customer', 'event_cancelled'))",
            name="ck_reservations_cancellation",
        ),
        UniqueConstraint("id", "event_id", name="uq_reservations_id_event"),
        UniqueConstraint("user_id", "event_id", "idempotency_key", name="uq_reservations_retry"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    # A reason distinguishes customer withdrawal from organizer cancellation.
    cancellation_reason: Mapped[str | None] = mapped_column(String(32))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReservationSeat(Base):
    """An active or released claim to an event seat, constrained to the booking's event."""

    __tablename__ = "reservation_seats"
    __table_args__ = (
        # Released rows remain history; only active claims must be unique.
        Index("uq_reservation_seats_event_seat", "event_id", "seat_id", unique=True, postgresql_where=text("released_at IS NULL")),
        ForeignKeyConstraint(["reservation_id", "event_id"], ["reservations.id", "reservations.event_id"], ondelete="RESTRICT", name="fk_reservation_seats_reservation"),
        ForeignKeyConstraint(["event_id", "seat_id"], ["event_seats.event_id", "event_seats.seat_id"], ondelete="RESTRICT", name="fk_reservation_seats_membership"),
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reservation_id: Mapped[UUID] = mapped_column(primary_key=True)
    seat_id: Mapped[UUID] = mapped_column(primary_key=True)
    event_id: Mapped[UUID] = mapped_column(nullable=False)
