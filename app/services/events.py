"""Event persistence and browsing, independent of HTTP routing."""

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import EventNotFound
from app.models import Event
from app.schemas.events import EventCreate


async def create_event(session: AsyncSession, payload: EventCreate) -> Event:
    """Commit validated event data and load generated fields."""
    event = Event(**payload.model_dump())
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def list_events(session: AsyncSession, *, limit: int, offset: int) -> list[Event]:
    """Include past events and break equal-start-time ties with UUID ordering."""
    result = await session.scalars(
        select(Event).order_by(Event.starts_at, Event.id).limit(limit).offset(offset)
    )
    return list(result)


async def get_event(session: AsyncSession, event_id: UUID) -> Event:
    """Return stored event data or raise an application-level missing-event error."""
    event = await session.get(Event, event_id)
    if event is None:
        raise EventNotFound()
    return event
