"""Bridge HTTP bearer credentials and application state to reusable operations."""

from typing import Annotated
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.errors import InvalidToken
from app.models import User
from app.security import decode_access_token
from app.services.auth import get_user

Session = Annotated[AsyncSession, Depends(get_session)]
bearer = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    """Keep the same error response and challenge for every authentication failure."""
    return HTTPException(401, "Invalid authentication credentials", headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(
    request: Request, session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    """Extract bearer credentials and resolve a verified subject into an account."""
    if credentials is None:
        raise unauthorized()
    try:
        user_id = decode_access_token(
            credentials.credentials, request.app.state.settings.jwt_secret.get_secret_value(),
        )
    except InvalidToken as exc:
        raise unauthorized() from exc
    user = await get_user(session, user_id)
    if user is None:
        raise unauthorized()
    return user


async def get_organizer(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Check current database permission so promotions work with existing tokens."""
    if not user.is_organizer:
        raise HTTPException(403, "Organizer permission required")
    return user


Organizer = Annotated[User, Depends(get_organizer)]
