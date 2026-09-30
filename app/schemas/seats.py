"""Validate explicit seat batches and serialize physical seats."""

from typing import Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SeatCreate(BaseModel):
    """Labels preserve case; positive numbering fits PostgreSQL INTEGER."""

    section: Label
    row: Label
    number: Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]


class SeatBatch(BaseModel):
    """Bound transaction size while supporting irregular seating layouts."""

    seats: Annotated[list[SeatCreate], Field(min_length=1, max_length=500)]


class SeatRead(SeatCreate):
    """Expose physical seat identity and its owning venue."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    venue_id: UUID


class EventSeatRead(SeatRead):
    """Public availability without exposing who booked a seat."""

    is_available: bool
