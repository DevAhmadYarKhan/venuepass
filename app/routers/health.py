"""Application liveness endpoint with no database dependency."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Report application liveness without requiring PostgreSQL to be available."""
    return {"status": "ok"}
