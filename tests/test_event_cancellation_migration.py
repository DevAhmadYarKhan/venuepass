"""Exercise the actual migration against temporary tables, preserving public data."""

import importlib.util
from pathlib import Path
import subprocess
import sys

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings


def test_offline_event_cancellation_sql():
    """Both directions render without querying live database contents."""
    upgrade = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', '20261001_0008:head', '--sql'], capture_output=True, text=True, check=True).stdout
    downgrade = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', '20261002_0009:20261001_0008', '--sql'], capture_output=True, text=True, check=True).stdout
    assert "SET cancellation_reason = 'customer' WHERE cancelled_at IS NOT NULL" in upgrade
    assert 'ck_reservations_cancellation' in upgrade
    assert downgrade.index('Cannot downgrade while event cancellation history exists') < downgrade.index('DROP COLUMN')


@pytest.mark.integration
async def test_upgrade_backfill_and_guarded_downgrade():
    """Run migration functions on temporary tables, retaining customer timestamps on rollback."""
    spec = importlib.util.spec_from_file_location('event_cancellation_revision', Path(__file__).resolve().parents[1] / 'alembic/versions/20261002_0009_event_cancellation.py')
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    url = str(Settings().test_database_url)
    assert make_url(url).database == 'venuepass_db_test'
    engine = create_async_engine(url)

    def migrate(connection, operation):
        """Bind real Alembic operations to the test's transaction and temporary tables."""
        with Operations.context(MigrationContext.configure(connection)):
            operation()

    try:
        async with engine.begin() as conn:
            # Temporary relations shadow public tables; the migration never edits app data.
            await conn.execute(text('CREATE TEMP TABLE events (id integer) ON COMMIT DROP'))
            await conn.execute(text('CREATE TEMP TABLE reservations (id integer, cancelled_at timestamptz) ON COMMIT DROP'))
            await conn.execute(text("INSERT INTO reservations VALUES (1, NULL), (2, '2030-01-01T00:00:00Z')"))
            await conn.run_sync(migrate, revision.upgrade)
            assert (await conn.execute(text('SELECT cancellation_reason FROM reservations ORDER BY id'))).scalars().all() == [None, 'customer']
            await conn.run_sync(migrate, revision.downgrade)
            assert await conn.scalar(text('SELECT cancelled_at IS NOT NULL FROM reservations WHERE id = 2'))
            await conn.run_sync(migrate, revision.upgrade)
            await conn.execute(text('INSERT INTO events (id, cancelled_at) VALUES (1, now())'))
            with pytest.raises(DBAPIError, match='Cannot downgrade while event cancellation history exists'):
                async with conn.begin_nested():
                    await conn.run_sync(migrate, revision.downgrade)
            await conn.execute(text('DELETE FROM events'))
            await conn.execute(text("UPDATE reservations SET cancelled_at = now(), cancellation_reason = 'event_cancelled' WHERE id = 1"))
            with pytest.raises(DBAPIError, match='Cannot downgrade while event cancellation history exists'):
                async with conn.begin_nested():
                    await conn.run_sync(migrate, revision.downgrade)
    finally:
        await engine.dispose()
