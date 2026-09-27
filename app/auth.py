"""Local account registration, password verification, and JWT bearer authentication."""

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.database import get_session
from app.models import User

router = APIRouter(tags=["authentication"])
bearer = HTTPBearer(auto_error=False)
Session = Annotated[AsyncSession, Depends(get_session)]
password_hasher = PasswordHash.recommended()
TOKEN_SECONDS = 1800


class Credentials(BaseModel):
    """Normalize account identifiers while preserving passwords exactly."""

    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        """Treat email addresses as case-insensitive account identifiers."""
        return value.strip().lower() if isinstance(value, str) else value


class Registration(Credentials):
    """Require a longer password when creating an account."""

    password: SecretStr = Field(min_length=15, max_length=128)


class UserRead(BaseModel):
    """Expose account identity without credential material."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    created_at: datetime


class TokenRead(BaseModel):
    """Describe the access token and its fixed lifetime in seconds."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = TOKEN_SECONDS


def unauthorized() -> HTTPException:
    """Use a consistent bearer challenge for authentication failures."""
    return HTTPException(401, "Invalid authentication credentials", headers={"WWW-Authenticate": "Bearer"})


@router.post("/auth/register", response_model=UserRead, status_code=201)
async def register(payload: Registration, session: Session) -> User:
    """Hash the password off the event loop and rely on database uniqueness."""
    hashed = await run_in_threadpool(password_hasher.hash, payload.password.get_secret_value())
    user = User(email=str(payload.email), password_hash=hashed)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Only translate the email constraint; unrelated database failures remain errors.
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "uq_users_email":
            raise HTTPException(409, "Email already registered") from exc
        raise
    await session.refresh(user)
    return user


@router.post("/auth/login", response_model=TokenRead)
async def login(payload: Credentials, request: Request, session: Session) -> TokenRead:
    """Verify credentials without revealing whether the account exists."""
    user = await session.scalar(select(User).where(User.email == str(payload.email)))
    # Unknown accounts still perform Argon2 verification to avoid a cheap timing path.
    hashed = user.password_hash if user else request.app.state.dummy_password_hash
    valid = await run_in_threadpool(password_hasher.verify, payload.password.get_secret_value(), hashed)
    if not valid or user is None:
        raise unauthorized()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": str(user.id), "iat": now, "exp": now + timedelta(seconds=TOKEN_SECONDS)},
        request.app.state.settings.jwt_secret.get_secret_value(), algorithm="HS256",
    )
    return TokenRead(access_token=token)


async def get_current_user(
    request: Request, session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    """Validate signature, required claims, expiry, and the persisted user identity."""
    if credentials is None:
        raise unauthorized()
    try:
        claims = jwt.decode(
            credentials.credentials, request.app.state.settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"], options={"require": ["sub", "iat", "exp"]},
        )
        user_id = UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise unauthorized() from exc
    user = await session.get(User, user_id)
    if user is None:
        raise unauthorized()
    return user


@router.get("/users/me", response_model=UserRead)
async def current_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Return the authenticated account for clients and future reservation ownership."""
    return user
