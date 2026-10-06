"""Verify the deployment startup gate without migrating a database or binding a port."""

import subprocess
import sys
from unittest.mock import Mock

from app import start


def test_successful_migration_precedes_server_exec(monkeypatch, capsys):
    """Migration must finish before the launcher hands its process to Uvicorn."""
    operations = Mock()
    monkeypatch.setattr(start.subprocess, "run", operations.migrate)
    monkeypatch.setattr(start.os, "execv", operations.serve)

    assert start.main() == 0
    operations.migrate.assert_called_once_with(
        [sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    operations.serve.assert_called_once_with(sys.executable, [sys.executable,
        "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"])
    assert [call[0] for call in operations.mock_calls] == ["migrate", "serve"]
    assert "Starting API" in capsys.readouterr().out


def test_failed_migration_prevents_server_start(monkeypatch, capsys):
    """Propagate the migration's exit status and never execute the application."""
    migrate = Mock(side_effect=subprocess.CalledProcessError(7, ["alembic"]))
    serve = Mock()
    monkeypatch.setattr(start.subprocess, "run", migrate)
    monkeypatch.setattr(start.os, "execv", serve)

    assert start.main() == 7
    serve.assert_not_called()
    assert "Starting API" not in capsys.readouterr().out
