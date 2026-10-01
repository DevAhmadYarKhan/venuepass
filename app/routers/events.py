"""Event HTTP endpoints delegating persistence and queries to services."""

from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import Organizer, Session
from app.errors import EventNotFound, VenueNotFound, EmptyVenue, VenueAccessDenied, OrganizerRequired, EventOwnershipRequired, EventCancellationConflict
from app.models import Event
from app.schemas.seats import EventSeatRead
from app.schemas.events import EventCreate, EventFilters, EventRead
from app.services import events

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=EventRead, status_code=status.HTTP_201_CREATED)
async def create_event(payload: EventCreate, session: Session, organizer: Organizer) -> Event:
    """Persist a validated event and return its generated fields."""
    try:
        return await events.create_event(session, payload, organizer_id=organizer.id)
    except VenueNotFound as exc:
        raise HTTPException(404, "Venue not found") from exc
    except (VenueAccessDenied, OrganizerRequired) as exc:
        raise HTTPException(403, "Organizer is not authorized for this venue") from exc
    except EmptyVenue as exc:
        raise HTTPException(409, "Venue has no seats") from exc


@router.get("", response_model=list[EventRead])
async def list_events(
    session: Session,
    filters: Annotated[EventFilters, Query()],
) -> list[Event]:
    """Browse publicly with optional combined filters and deterministic pagination."""
    return await events.list_events(session, filters=filters)


@router.get("/{event_id}", response_model=EventRead)
async def get_event(event_id: UUID, session: Session) -> Event:
    """Retrieve an event or report that the requested UUID does not exist."""
    try:
        return await events.get_event(session, event_id)
    except EventNotFound as exc:
        raise HTTPException(404, detail="Event not found") from exc


@router.get("/{event_id}/seats", response_model=list[EventSeatRead])
async def list_event_seats(event_id: UUID, session: Session,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EventSeatRead]:
    """Browse fixed event seats with current public availability."""
    try:
        return await events.list_event_seats(session, event_id, limit=limit, offset=offset)
    except EventNotFound as exc:
        raise HTTPException(404, "Event not found") from exc


@router.post("/{event_id}/cancel", response_model=EventRead)
async def cancel_event(event_id: UUID, session: Session, organizer: Organizer) -> Event:
    """Let the owning organizer cancel without requiring current venue access."""
    try:
        return await events.cancel_event(session, event_id, organizer_id=organizer.id)
    except EventNotFound as exc:
        raise HTTPException(404, "Event not found") from exc
    except (OrganizerRequired, EventOwnershipRequired) as exc:
        raise HTTPException(403, "Event owner with organizer permission required") from exc
    except EventCancellationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
