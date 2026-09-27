"""Verify liveness and documentation are available without a database connection."""

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


async def test_health_without_database():
    """Exercise startup and HTTP routes with an intentionally unreachable DB URL."""
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused:unused@127.0.0.1:1/unavailable",
    )
    app = create_app(settings)
    # ASGITransport does not trigger lifespan events, so run them explicitly to
    # cover engine initialization and shutdown as well as the HTTP responses.
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            assert (await client.get("/docs")).status_code == 200
