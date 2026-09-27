"""Authentication input validation and access-token responses."""

from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator
from app.security import TOKEN_SECONDS


class Credentials(BaseModel):
    """Normalize account identifiers while preserving passwords exactly."""

    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        """Treat email addresses as case-insensitive account identifiers."""
        return value.strip().lower() if isinstance(value, str) else value


class Registration(Credentials):
    """Require a longer password when creating an account."""

    password: SecretStr = Field(min_length=15, max_length=128)


class TokenRead(BaseModel):
    """Describe the access token and its fixed lifetime in seconds."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = TOKEN_SECONDS


