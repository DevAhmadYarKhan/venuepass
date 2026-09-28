"""Owner-only HTTP endpoints for managing event organizer access."""

from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, Response
from app.dependencies import Session, VenueManager
from app.errors import VenueNotFound, UserNotFound, OrganizerRequired, VenueOwnershipRequired
from app.services import venue_access

router = APIRouter(prefix="/venues/{venue_id}/organizers", tags=["venues"])


def access_error(exc: Exception) -> HTTPException:
    """Translate only known application errors at the HTTP boundary."""
    if isinstance(exc, VenueOwnershipRequired):
        return HTTPException(403, "Venue ownership required")
    if isinstance(exc, OrganizerRequired):
        return HTTPException(409, "User is not an organizer")
    return HTTPException(404, "Venue not found" if isinstance(exc, VenueNotFound) else "User not found")


@router.put("/{organizer_id}", status_code=204)
async def grant_access(venue_id: UUID, organizer_id: UUID, session: Session, manager: VenueManager) -> Response:
    """Authorize future event creation, including repeated identical grants."""
    try:
        await venue_access.change_access(session, venue_id, organizer_id, manager.id, grant=True)
    except (VenueNotFound, UserNotFound, OrganizerRequired, VenueOwnershipRequired) as exc:
        raise access_error(exc) from exc
    return Response(status_code=204)


@router.delete("/{organizer_id}", status_code=204)
async def revoke_access(venue_id: UUID, organizer_id: UUID, session: Session, manager: VenueManager) -> Response:
    """Revoke future creation rights without modifying previously created events."""
    try:
        await venue_access.change_access(session, venue_id, organizer_id, manager.id, grant=False)
    except (VenueNotFound, UserNotFound, VenueOwnershipRequired) as exc:
        raise access_error(exc) from exc
    return Response(status_code=204)


@router.get("", response_model=list[UUID])
async def list_organizers(venue_id: UUID, session: Session, manager: VenueManager,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UUID]:
    """List explicitly authorized organizer IDs for the venue owner."""
    try:
        return await venue_access.list_organizers(session, venue_id, manager.id, limit=limit, offset=offset)
    except (VenueNotFound, VenueOwnershipRequired) as exc:
        raise access_error(exc) from exc
