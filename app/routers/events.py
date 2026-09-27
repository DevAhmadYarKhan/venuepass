"""Event HTTP endpoints delegating persistence and queries to services."""

from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import Session
from app.errors import EventNotFound
from app.models import Event
from app.schemas.events import EventCreate, EventRead
from app.services import events

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=EventRead, status_code=status.HTTP_201_CREATED)
async def create_event(payload: EventCreate, session: Session) -> Event:
    """Persist a validated event and return its generated fields."""
    return await events.create_event(session, payload)


@router.get("", response_model=list[EventRead])
async def list_events(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Event]:
    """Browse all events, including past ones, with deterministic pagination."""
    return await events.list_events(session, limit=limit, offset=offset)


@router.get("/{event_id}", response_model=EventRead)
async def get_event(event_id: UUID, session: Session) -> Event:
    """Retrieve an event or report that the requested UUID does not exist."""
    try:
        return await events.get_event(session, event_id)
    except EventNotFound as exc:
        raise HTTPException(404, detail="Event not found") from exc
