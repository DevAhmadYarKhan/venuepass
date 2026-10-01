"""Event request validation and public response fields."""

from datetime import datetime, timezone
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_serializer, model_validator,
)

EventLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class EventCreate(BaseModel):
    """Validate creation input before opening a database transaction."""

    name: EventLabel
    description: str | None = None
    venue_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    @model_validator(mode="before")
    @classmethod
    def reject_legacy_fields(cls, value):
        """Fail explicitly when clients still supply venue text or capacity."""
        if isinstance(value, dict) and ({"venue", "capacity"} & value.keys()):
            raise ValueError("Use venue_id; capacity is derived from venue seats")
        return value

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        """Require future starts and, when supplied, a later finish."""
        if self.starts_at <= datetime.now(timezone.utc):
            raise ValueError("starts_at must be in the future")
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class EventRead(BaseModel):
    """Serialize stored events, including events whose start time has passed."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organizer_id: UUID
    name: str
    description: str | None
    venue_id: UUID
    starts_at: datetime
    ends_at: datetime | None
    cancelled_at: datetime | None

    @field_serializer("cancelled_at")
    def serialize_cancellation(self, value: datetime | None) -> datetime | None:
        """Keep first responses and subsequent reads identical across database timezones."""
        return value.astimezone(timezone.utc) if value is not None else None

    capacity: int
    created_at: datetime


class EventFilters(BaseModel):
    """Validate optional discovery filters directly from URL query parameters."""

    q: EventLabel | None = Field(default=None, description="Case-insensitive literal substring of the event name")
    venue_id: UUID | None = Field(default=None, description="Only events at this venue")
    starts_from: AwareDatetime | None = Field(default=None, description="Inclusive event start-time lower bound; timezone required")
    starts_before: AwareDatetime | None = Field(default=None, description="Exclusive event start-time upper bound; timezone required")
    upcoming_only: bool = Field(default=False, description="Only events starting strictly after the current UTC time")
    include_cancelled: bool = Field(default=False, description="Include cancelled events in the filtered results")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum number of matching events")
    offset: int = Field(default=0, ge=0, description="Matching events to skip")

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        """Reject empty or reversed ranges while allowing either bound on its own."""
        if self.starts_from is not None and self.starts_before is not None:
            if self.starts_from >= self.starts_before:
                raise ValueError("starts_from must be before starts_before")
        return self
