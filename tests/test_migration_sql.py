"""Keep offline migration rendering and the legacy-event guard working together."""

from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings


@pytest.fixture(scope='module')
def migration_sql():
    """Render the actual migration chain without opening a database connection."""
    result = subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', 'head', '--sql'],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def test_offline_upgrade_contains_guard(migration_sql):
    """The safety check must be emitted before the event schema changes."""
    assert 'Existing events require explicit venue mapping' in migration_sql
    assert migration_sql.index('IF EXISTS (SELECT 1 FROM events)') < migration_sql.index('ALTER TABLE events ADD COLUMN venue_id')


@pytest.mark.integration
async def test_rendered_guard_checks_existing_events(migration_sql):
    """Execute the emitted guard against a temporary table, preserving real data."""
    url = str(Settings().test_database_url)
    assert make_url(url).database == 'venuepass_db_test'
    engine = create_async_engine(url)
    start = migration_sql.index('DO $$')
    guard = migration_sql[start:migration_sql.index('$$;', start) + 3]
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                # PostgreSQL resolves the temporary events table before public.events.
                await connection.execute(text('CREATE TEMP TABLE events (id integer) ON COMMIT DROP'))
                await connection.execute(text(guard))
                await connection.execute(text('INSERT INTO events VALUES (1)'))
                with pytest.raises(DBAPIError, match='Existing events require explicit venue mapping'):
                    async with connection.begin_nested():
                        await connection.execute(text(guard))
                assert await connection.scalar(text('SELECT count(*) FROM events')) == 1
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
