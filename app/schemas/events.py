"""Event request validation and public response fields."""

from datetime import datetime, timezone
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator,
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
    capacity: int
    created_at: datetime
