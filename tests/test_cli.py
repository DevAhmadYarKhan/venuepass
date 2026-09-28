"""Verify CLI parsing and actual database-backed organizer promotion."""

from unittest.mock import AsyncMock
import pytest

from app import cli
from app.errors import UserNotFound


@pytest.mark.parametrize("command", ["promote-organizer", "promote-venue-manager"])
def test_cli_normalization_and_errors(monkeypatch, capsys, command):
    """The command normalizes email and reports invalid/missing accounts clearly."""
    promote = AsyncMock()
    monkeypatch.setattr(cli, 'promote', promote)
    assert cli.main([command, ' User@Example.com ']) == 0
    promote.assert_awaited_once_with('user@example.com', command)
    assert 'enabled' in capsys.readouterr().out
    promote.side_effect = UserNotFound()
    assert cli.main([command, 'missing@example.com']) == 1
    assert 'No account found' in capsys.readouterr().err
    assert cli.main([command, 'invalid']) == 1
    assert 'Invalid email' in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.parametrize("command,flag", [("promote-organizer", "is_organizer"), ("promote-venue-manager", "is_venue_manager")])
async def test_cli_database_promotion(command, flag):
    """Exercise the real command against a committed test account, then clean up."""
    import asyncio
    import os
    from uuid import uuid4
    from sqlalchemy import delete, select
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.config import Settings
    from app.models import User

    settings = Settings()
    url = str(settings.test_database_url)
    assert make_url(url).database == 'venuepass_db_test'
    engine = create_async_engine(url)
    user_id = uuid4()
    email = f'cli-{user_id}@example.com'
    env = dict(os.environ, DATABASE_URL=url)
    try:
        async with engine.begin() as connection:
            await connection.execute(User.__table__.insert().values(
                id=user_id, email=email, password_hash='unused',
            ))
        for _ in range(2):
            process = await asyncio.create_subprocess_exec(
                '.venv/bin/python', '-m', 'app.cli', command, f' {email.upper()} ',
                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await process.communicate()
            assert process.returncode == 0, err.decode()
            assert 'enabled' in out.decode()
        async with engine.connect() as connection:
            assert await connection.scalar(select(getattr(User, flag)).where(User.id == user_id)) is True
        process = await asyncio.create_subprocess_exec(
            '.venv/bin/python', '-m', 'app.cli', command, f'missing-{uuid4()}@example.com',
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await process.communicate()
        assert process.returncode == 1
        assert 'No account found' in err.decode()
    finally:
        async with engine.begin() as connection:
            await connection.execute(delete(User).where(User.id == user_id))
        await engine.dispose()
