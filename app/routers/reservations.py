"""Authenticated booking endpoints and application-error translation."""

from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from app.dependencies import Session, get_current_user
from app.models import User
from app.errors import BookingConflict, EventNotFound, InvalidReservationSeats, ReservationNotFound
from app.schemas.reservations import ReservationCreate, ReservationRead
from app.services import reservations

router = APIRouter(tags=["reservations"])
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("/events/{event_id}/reservations", response_model=ReservationRead, status_code=201)
async def create_reservation(event_id: UUID, payload: ReservationCreate, session: Session, user: CurrentUser,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128, pattern=r"^[!-~]+$")],
) -> ReservationRead:
    """Book all requested seats or recover a previous successful request."""
    try:
        return await reservations.create_reservation(session, event_id, user.id, payload.seat_ids, idempotency_key)
    except EventNotFound as exc:
        raise HTTPException(404, "Event not found") from exc
    except InvalidReservationSeats as exc:
        raise HTTPException(422, "Seats must belong to the event") from exc
    except BookingConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/reservations/{reservation_id}", response_model=ReservationRead)
async def get_reservation(reservation_id: UUID, session: Session, user: CurrentUser) -> ReservationRead:
    """Return only a reservation owned by the authenticated user."""
    try:
        return await reservations.get_reservation(session, reservation_id, user.id)
    except ReservationNotFound as exc:
        raise HTTPException(404, "Reservation not found") from exc


@router.get("/users/me/reservations", response_model=list[ReservationRead])
async def list_reservations(session: Session, user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ReservationRead]:
    """Browse the current user's confirmed bookings."""
    return await reservations.list_reservations(session, user.id, limit=limit, offset=offset)
