"""Application settings, loaded once from the environment."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Agentic SQL Analyst"
    version: str = "1.0.0"
    debug: bool = False

    anthropic_api_key: str = ""
    model: str = "claude-opus-5"
    use_adaptive_thinking: bool = True

    # postgresql+psycopg:// selects the psycopg 3 driver.
    database_url: str = Field(
        default="postgresql+psycopg://analyst_ro:analyst_ro@localhost:5432/analytics"
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # Guardrail limits. max_rows caps the payload we return; statement_timeout
    # caps the work the server does. They are not the same control -- a
    # LIMIT 10 over a billion-row aggregate still burns a CPU for minutes.
    max_rows: int = 1000
    statement_timeout_ms: int = 5000

    max_retries: int = 3

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so the environment is parsed exactly once."""
    return Settings()
