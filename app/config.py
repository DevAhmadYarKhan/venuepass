"""Load and validate configuration shared by the API and migration commands."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve from this module so .env loading does not depend on the working directory.
ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Database URLs, with environment variables taking precedence over .env."""

    # Ignore unrelated dotenv entries so other local tools can share the file.
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # No default signing key: each environment must supply its own secret.
    jwt_secret: SecretStr = Field(min_length=32)
    database_url: PostgresDsn
    # Running the API does not require a test database; integration tests do.
    test_database_url: PostgresDsn | None = None


@lru_cache
def get_settings() -> Settings:
    """Validate settings once per process and reuse them on subsequent calls."""
    return Settings()
