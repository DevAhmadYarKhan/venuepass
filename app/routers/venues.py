"""Venue HTTP endpoints and application-error translation."""

from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from app.dependencies import Session, VenueManager
from app.errors import VenueNotFound
from app.models import Venue
from app.schemas.venues import VenueCreate, VenueRead
from app.services import venues

router = APIRouter(prefix="/venues", tags=["venues"])


@router.post("", response_model=VenueRead, status_code=201)
async def create_venue(payload: VenueCreate, session: Session, manager: VenueManager) -> Venue:
    """Create a venue owned by the authenticated venue manager."""
    return await venues.create_venue(session, payload, owner_id=manager.id)


@router.get("", response_model=list[VenueRead])
async def list_venues(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Venue]:
    """Browse venues publicly with bounded page sizes."""
    return await venues.list_venues(session, limit=limit, offset=offset)


@router.get("/{venue_id}", response_model=VenueRead)
async def get_venue(venue_id: UUID, session: Session) -> Venue:
    """Retrieve a public venue or return the corresponding HTTP error."""
    try:
        return await venues.get_venue(session, venue_id)
    except VenueNotFound as exc:
        raise HTTPException(404, "Venue not found") from exc
