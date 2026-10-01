"""Reservation input and public booking details, excluding retry keys."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReservationCreate(BaseModel):
    """Require a small, distinct collection of event seat IDs."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{"seat_ids": ["12345678-1234-4234-8234-123456789abc"]}]
    })
    seat_ids: Annotated[list[UUID], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def distinct_seats(self) -> Self:
        """Reject duplicates rather than silently changing the requested quantity."""
        if len(set(self.seat_ids)) != len(self.seat_ids):
            raise ValueError("seat_ids must be distinct")
        return self


class ReservationRead(BaseModel):
    """Stable response used for initial creation, retries, detail, and history."""

    id: UUID
    event_id: UUID
    user_id: UUID
    seat_ids: list[UUID]
    created_at: datetime
    cancelled_at: datetime | None
