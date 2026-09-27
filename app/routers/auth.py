"""Authentication endpoints translating application results into HTTP responses."""

from fastapi import APIRouter, HTTPException, Request
from app.dependencies import Session, unauthorized
from app.errors import DuplicateEmail, InvalidCredentials
from app.models import User
from app.schemas.auth import Credentials, Registration, TokenRead
from app.schemas.users import UserRead
from app.services import auth

router = APIRouter(tags=["authentication"])


@router.post("/auth/register", response_model=UserRead, status_code=201)
async def register(payload: Registration, session: Session) -> User:
    """Hash the password off the event loop and rely on database uniqueness."""
    try:
        return await auth.register(session, email=str(payload.email), password=payload.password.get_secret_value())
    except DuplicateEmail as exc:
        raise HTTPException(409, "Email already registered") from exc


@router.post("/auth/login", response_model=TokenRead)
async def login(payload: Credentials, request: Request, session: Session) -> TokenRead:
    """Verify credentials without revealing whether the account exists."""
    try:
        token = await auth.login(
            session, email=str(payload.email), password=payload.password.get_secret_value(),
            dummy_password_hash=request.app.state.dummy_password_hash,
            secret=request.app.state.settings.jwt_secret.get_secret_value(),
        )
    except InvalidCredentials as exc:
        raise unauthorized() from exc
    return TokenRead(access_token=token)
