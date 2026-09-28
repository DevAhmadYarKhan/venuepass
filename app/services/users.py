"""Account management operations invoked by trusted local administration."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.errors import UserNotFound
from app.models import User


async def promote_organizer(session: AsyncSession, email: str) -> User:
    """Grant organizer permission idempotently to an existing account."""
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        raise UserNotFound()
    user.is_organizer = True
    await session.commit()
    await session.refresh(user)
    return user


async def promote_venue_manager(session: AsyncSession, email: str) -> User:
    """Grant venue management without changing organizer permission."""
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        raise UserNotFound()
    user.is_venue_manager = True
    await session.commit()
    await session.refresh(user)
    return user
