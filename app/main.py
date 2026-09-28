"""Construct the FastAPI application and manage its database engine lifecycle."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import secrets

from app.routers import auth, events, health, users, venues, seats
from app.security import hash_password
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app, optionally using explicit settings for isolated tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Create database resources at startup and release them at shutdown."""
        config = settings if settings is not None else get_settings()
        app.state.settings = config
        # Compute the dummy hash off the event loop once per application lifespan.
        app.state.dummy_password_hash = await hash_password(secrets.token_urlsafe(32))
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
    app.include_router(events.router)
    app.include_router(venues.router)
    app.include_router(seats.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(health.router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        """Omit submitted values so malformed credentials cannot leak in errors."""
        errors = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    return app


# Uvicorn imports this instance through the app.main:app entrypoint.
app = create_app()
