"""Public account fields, excluding stored credentials."""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class UserRead(BaseModel):
    """Expose account identity without credential material."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    created_at: datetime


