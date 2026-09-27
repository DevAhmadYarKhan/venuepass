"""Construct the FastAPI application and manage its database engine lifecycle."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app, optionally using explicit settings for isolated tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Create database resources at startup and release them at shutdown."""
        config = settings if settings is not None else get_settings()
        # Engine creation is lazy: startup does not open a database connection.
        # Pre-ping checks pooled connections before a later request uses them.
        engine = create_async_engine(str(config.database_url), pool_pre_ping=True)
        # Retain loaded attributes after commits to avoid implicit async I/O.
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            yield
        finally:
            # Release pooled connections even when the application exits on error.
            await engine.dispose()

    app = FastAPI(title="VenuePass API", lifespan=lifespan)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        """Report application liveness without requiring PostgreSQL to be available."""
        return {"status": "ok"}

    return app


# Uvicorn imports this instance through the app.main:app entrypoint.
app = create_app()
