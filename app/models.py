"""Persisted event data and database-level invariants."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, false, func
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
