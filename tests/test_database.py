"""Read-only integration checks against the explicitly configured test database."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings


@pytest.mark.integration
async def test_test_database_connection():
    """Verify async connectivity and the identity of the connected database."""
    settings = Settings()
    # Never fall back to DATABASE_URL or silently target the application database.
    assert settings.test_database_url is not None, "Set TEST_DATABASE_URL to run integration tests"
    url = str(settings.test_database_url)
    assert make_url(url).database == "venuepass_db_test", "Integration tests require venuepass_db_test"
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
            assert await connection.scalar(text("SELECT current_database()")) == "venuepass_db_test"
    finally:
        # Release connections even when a query or assertion fails.
        await engine.dispose()
