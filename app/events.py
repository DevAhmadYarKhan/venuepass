"""Create and browse events through request-scoped async sessions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Event
from app.schemas import EventCreate, EventRead

router = APIRouter(prefix="/events", tags=["events"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=EventRead, status_code=status.HTTP_201_CREATED)
async def create_event(payload: EventCreate, session: Session) -> Event:
    """Persist a validated event and return its generated fields."""
    event = Event(**payload.model_dump())
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


@router.get("", response_model=list[EventRead])
async def list_events(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Event]:
    """Browse all events, including past ones, with deterministic pagination."""
    # UUID breaks ties when multiple events share a start time.
    result = await session.scalars(
        select(Event).order_by(Event.starts_at, Event.id).limit(limit).offset(offset)
    )
    return list(result)


@router.get("/{event_id}", response_model=EventRead)
async def get_event(event_id: UUID, session: Session) -> Event:
    """Retrieve an event or report that the requested UUID does not exist."""
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event
