"""Load and validate configuration shared by the API and migration commands."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve from this module so .env loading does not depend on the working directory.
ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Database and auth settings; environment variables take precedence over .env."""

    # Ignore unrelated dotenv entries so other local tools can share the file.
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # No default signing key: each environment must supply its own secret.
    jwt_secret: SecretStr = Field(min_length=32)
    database_url: PostgresDsn
    # Running the API does not require a test database; integration tests do.
    test_database_url: PostgresDsn | None = None
    # Native development can omit Redis; public deployments must configure it.
    redis_url: RedisDsn | None = None
    test_redis_url: RedisDsn | None = None
    auth_login_limit: int = Field(default=10, gt=0)
    auth_login_window_seconds: int = Field(default=60, gt=0)
    auth_register_limit: int = Field(default=5, gt=0)
    auth_register_window_seconds: int = Field(default=3600, gt=0)


@lru_cache
def get_settings() -> Settings:
    """Validate settings once per process and reuse them on subsequent calls."""
    return Settings()
