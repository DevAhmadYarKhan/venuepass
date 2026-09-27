"""Account registration, login, and lookup using explicit database sessions."""

from uuid import UUID
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DuplicateEmail, InvalidCredentials
from app.models import User
from app.security import create_access_token, hash_password, verify_password


async def register(session: AsyncSession, *, email: str, password: str) -> User:
    """Persist an account; PostgreSQL resolves concurrent duplicate registrations."""
    user = User(email=email, password_hash=await hash_password(password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Translate only the known email conflict, preserving unrelated failures.
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "uq_users_email":
            raise DuplicateEmail() from exc
        raise
    await session.refresh(user)
    return user


async def login(
    session: AsyncSession, *, email: str, password: str,
    dummy_password_hash: str, secret: str,
) -> str:
    """Authenticate credentials and issue a token without exposing account existence."""
    user = await session.scalar(select(User).where(User.email == email))
    # Unknown accounts perform the same costly password-verification operation.
    hashed = user.password_hash if user else dummy_password_hash
    valid = await verify_password(password, hashed)
    if not valid or user is None:
        raise InvalidCredentials()
    return create_access_token(user.id, secret)


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    """Resolve a token subject against the current persisted accounts."""
    return await session.get(User, user_id)
