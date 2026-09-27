"""Persisted event data and database-level invariants."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Event(Base):
    """An event whose capacity will support future ticket reservations."""

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="ck_events_capacity_positive"),
        CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at", name="ck_events_end_after_start"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str] = mapped_column(String(255))
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
