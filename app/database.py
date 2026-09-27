"""Shared model metadata and request-scoped async database sessions."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base for future ORM models; Alembic reads this shared table metadata."""

    pass


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one session per request; callers explicitly commit writes."""
    # The lifespan creates this factory. Closing the session also rolls back any
    # uncommitted transaction, including when a request handler raises an error.
    async with request.app.state.session_factory() as session:
        yield session
