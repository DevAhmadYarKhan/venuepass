"""HTTP access to the authenticated user's public identity."""

from typing import Annotated
from fastapi import APIRouter, Depends
from app.dependencies import get_current_user
from app.models import User
from app.schemas.users import UserRead

router = APIRouter(tags=["authentication"])


@router.get("/users/me", response_model=UserRead)
async def current_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Return the authenticated account for clients and future reservation ownership."""
    return user
