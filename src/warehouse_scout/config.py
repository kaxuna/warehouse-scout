"""Typed application settings, read from environment variables (and `.env` locally).

Java analogy: a `@ConfigurationProperties` class. Field `postgres_host` is filled
from the env var `POSTGRES_HOST`; pydantic converts and validates the type.

Secrets use `SecretStr`, which prints as `**********` so a stray log line or
traceback cannot leak them. Call `.get_secret_value()` at the point of use.
"""

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic
    anthropic_api_key: SecretStr | None = None
    scout_model: str = "claude-opus-5-5"

    # Postgres: Scout's own database and the migration target
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "scout"
    postgres_user: str
    postgres_password: SecretStr
    postgres_ro_user: str
    postgres_ro_password: SecretStr

    # Oracle: the source warehouse
    oracle_host: str = "localhost"
    oracle_port: int = 1521
    oracle_service: str = "FREEPDB1"
    oracle_user: str
    oracle_password: SecretStr
    oracle_ro_user: str
    oracle_ro_password: SecretStr

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _empty_key_means_unset(cls, value: object) -> object:
        # `.env.example` ships the line `ANTHROPIC_API_KEY=`. Left unfilled, that
        # is an empty string, which must count as "no key", not as a key.
        return value or None

    @property
    def oracle_dsn(self) -> str:
        return f"{self.oracle_host}:{self.oracle_port}/{self.oracle_service}"


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process. Fails fast if a required variable is missing."""
    # Required fields are supplied by the environment, which mypy cannot see.
    return Settings()  # type: ignore[call-arg]
