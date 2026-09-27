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
    venue: EventLabel
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    # Strict integers reject booleans/fractions; the upper bound fits PostgreSQL INTEGER.
    capacity: Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]

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
    name: str
    description: str | None
    venue: str
    starts_at: datetime
    ends_at: datetime | None
    capacity: int
    created_at: datetime
