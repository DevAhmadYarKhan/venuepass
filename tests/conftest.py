"""Shared PostgreSQL fixtures for transaction-isolated endpoint tests."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from app.config import Settings
from app.database import get_session
from app.main import create_app

@pytest.fixture
async def event_client():
    """Let endpoint commits finish savepoints while an outer transaction rolls back."""
    settings = Settings(jwt_secret="test-signing-secret-that-is-at-least-32-characters", )
    assert settings.test_database_url is not None, "Set TEST_DATABASE_URL"
    url = str(settings.test_database_url)
    assert make_url(url).database == "venuepass_db_test"
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()

            async def test_session():
                """Bind each request to the test transaction without committing it."""
                async with AsyncSession(
                    bind=connection, expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    yield session

            app = create_app(Settings(jwt_secret="test-signing-secret-that-is-at-least-32-characters", database_url=url, _env_file=None))
            app.dependency_overrides[get_session] = test_session
            try:
                async with app.router.lifespan_context(app):
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        yield client, connection
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


