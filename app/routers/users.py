"""HTTP access to the authenticated user's identity and organizer event history."""

from typing import Annotated
from fastapi import APIRouter, Depends, Query
from app.dependencies import Organizer, VenueManager, Session, get_current_user
from app.models import Event, User, Venue
from app.schemas.users import UserRead
from app.schemas.events import EventRead
from app.services import events, venues
from app.schemas.venues import VenueRead

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


@router.get("/users/me/venues", response_model=list[VenueRead], tags=["venues"])
async def list_owned_venues(session: Session, manager: VenueManager,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Venue]:
    """Return only venues owned by the currently authorized venue manager."""
    return await venues.list_managed_venues(session, manager.id, hosting=False,
        limit=limit, offset=offset)


@router.get("/users/me/hosting-venues", response_model=list[VenueRead], tags=["venues"])
async def list_hosting_venues(session: Session, organizer: Organizer,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Venue]:
    """Return owned and explicitly authorized venues for future event creation."""
    return await venues.list_managed_venues(session, organizer.id, hosting=True,
        limit=limit, offset=offset)
