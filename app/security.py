"""Password hashing and JWT handling without HTTP or database dependencies."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

from app.errors import InvalidToken

password_hasher = PasswordHash.recommended()
TOKEN_SECONDS = 1800


async def hash_password(password: str) -> str:
    """Run CPU-intensive Argon2 hashing outside the event loop."""
    return await run_in_threadpool(password_hasher.hash, password)


async def verify_password(password: str, hashed: str) -> bool:
    """Run Argon2 verification outside the event loop, including dummy checks."""
    return await run_in_threadpool(password_hasher.verify, password, hashed)


def create_access_token(user_id: UUID, secret: str) -> str:
    """Sign the account identity with the existing fixed access-token lifetime."""
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user_id), "iat": now, "exp": now + timedelta(seconds=TOKEN_SECONDS)},
        secret, algorithm="HS256",
    )


def decode_access_token(token: str, secret: str) -> UUID:
    """Validate required claims and return the subject as a user identifier."""
    try:
        claims = jwt.decode(
            token, secret, algorithms=["HS256"],
            options={"require": ["sub", "iat", "exp"]},
        )
        return UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise InvalidToken() from exc
