"""Migrate then start one deployed API instance without shell command parsing."""

import os
import subprocess
import sys


def main() -> int:
    """Stop on migration failure; replace the launcher with Uvicorn after success."""
    print("Applying database migrations...", flush=True)
    try:
        # Use the image's interpreter and inherit its environment and working
        # directory, so Alembic sees the configured database and migration files.
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    except subprocess.CalledProcessError as exc:
        # Preserve the migration failure for Render and never start against an
        # outdated schema. Alembic's output goes directly to deployment logs.
        return exc.returncode

    print("Starting API...", flush=True)
    # Replacing this process lets the container deliver shutdown signals directly
    # to Uvicorn. Port 8000 matches the existing Docker image's health check.
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "0.0.0.0", "--port", "8000"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
