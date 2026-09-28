"""Venue seat endpoints and HTTP translation for ownership and conflicts."""

from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from app.dependencies import Session, VenueManager
from app.errors import DuplicateSeat, VenueNotFound, VenueOwnershipRequired
from app.models import Seat
from app.schemas.seats import SeatBatch, SeatRead
from app.services import seats

router = APIRouter(prefix="/venues/{venue_id}/seats", tags=["seats"])


@router.post("", response_model=list[SeatRead], status_code=201)
async def create_seats(venue_id: UUID, payload: SeatBatch, session: Session, manager: VenueManager) -> list[Seat]:
    """Create an owner's complete batch and return seats in submitted order."""
    try:
        return await seats.create_seats(session, venue_id, manager.id, payload)
    except VenueNotFound as exc:
        raise HTTPException(404, "Venue not found") from exc
    except VenueOwnershipRequired as exc:
        raise HTTPException(403, "Venue ownership required") from exc
    except DuplicateSeat as exc:
        raise HTTPException(409, "Duplicate seat") from exc


@router.get("", response_model=list[SeatRead])
async def list_seats(
    venue_id: UUID, session: Session,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Seat]:
    """Browse physical seats publicly; no booking availability is implied."""
    try:
        return await seats.list_seats(session, venue_id, limit=limit, offset=offset)
    except VenueNotFound as exc:
        raise HTTPException(404, "Venue not found") from exc
