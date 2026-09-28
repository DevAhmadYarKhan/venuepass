"""Venue input validation and public response fields."""

from datetime import datetime
from typing import Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, StringConstraints


class VenueCreate(BaseModel):
    """Accept venue details only; ownership is supplied by authentication."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    address: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class VenueRead(BaseModel):
    """Expose venue identity, location, and its owner's identifier."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    address: str
    owner_id: UUID
    created_at: datetime
