"""Check settings behavior independently of the developer's local environment."""

from app.config import Settings


def test_dotenv_loading_and_environment_override(tmp_path, monkeypatch):
    """Load both URLs from dotenv, then prove environment values override it."""
    # Remove inherited values so they cannot mask the temporary dotenv fixture.
    monkeypatch.setenv("JWT_SECRET", "test-signing-secret-that-is-at-least-32-characters")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    dotenv = tmp_path / ".env"
    file_url = "postgresql+psycopg://user:password@localhost/from_file"
    test_url = "postgresql+psycopg://user:password@localhost/from_file_test"
    dotenv.write_text(f"DATABASE_URL={file_url}\nTEST_DATABASE_URL={test_url}\n")
    settings = Settings(_env_file=dotenv)
    assert str(settings.database_url) == file_url
    assert str(settings.test_database_url) == test_url

    env_url = "postgresql+psycopg://user:password@localhost/from_environment"
    # A fresh settings instance must pick up the deployment-style override.
    monkeypatch.setenv("DATABASE_URL", env_url)
    assert str(Settings(_env_file=dotenv).database_url) == env_url
