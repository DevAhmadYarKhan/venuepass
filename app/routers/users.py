"""HTTP access to the authenticated user's identity and organizer event history."""

from typing import Annotated
from fastapi import APIRouter, Depends, Query
from app.dependencies import Organizer, Session, get_current_user
from app.models import Event, User
from app.schemas.users import UserRead
from app.schemas.events import EventRead
from app.services import events

router = APIRouter(tags=["authentication"])


@router.get("/users/me", response_model=UserRead)
async def current_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Return the authenticated account for clients and future reservation ownership."""
    return user


@router.get("/users/me/events", response_model=list[EventRead], tags=["events"])
async def list_owned_events(session: Session, organizer: Organizer,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Event]:
    """Browse only the current organizer's events, including past and cancelled ones."""
    return await events.list_organizer_events(session, organizer.id, limit=limit, offset=offset)
