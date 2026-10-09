"""Runtime settings.

Required environment variables, with no built-in secret:
DATABASE_URL, FILE_DATA_DIR.
FILE_MAX_BYTES is optional and is not a credential.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process settings loaded only from the environment."""

    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8"
    )

    database_url: str = Field(
        default="sqlite:///./data/app.db",
        validation_alias="DATABASE_URL"
    )
    file_data_dir: Path = Field(
        default="./data/files",
        validation_alias="FILE_DATA_DIR"
    )
    file_max_bytes: int = Field(
        default=20 * 1024 * 1024,
        validation_alias="FILE_MAX_BYTES",
        gt=0,
    )
    ai_base_url: str = Field(default="", validation_alias="AI_BASE_URL")
    ai_model: str = Field(default="", validation_alias="AI_MODEL")
    ai_api_key: str = Field(default="", validation_alias="AI_API_KEY")
    ai_context_tokens: int = Field(default=32000, ge=8000, validation_alias="AI_CONTEXT_TOKENS")
    ai_reasoning_effort: Literal["", "none", "minimal", "low", "medium", "high", "xhigh", "max"] = Field(
        default="", validation_alias="AI_REASONING_EFFORT"
    )

    @field_validator("ai_reasoning_effort", mode="before")
    @classmethod
    def normalize_reasoning_effort(cls, value: str) -> str:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("database_url")
    @classmethod
    def database_url_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("DATABASE_URL is required")
        return cleaned

    @field_validator("file_data_dir")
    @classmethod
    def data_dir_required(cls, value: Path) -> Path:
        if not str(value).strip():
            raise ValueError("FILE_DATA_DIR is required")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def ai_reasoning_kwargs() -> dict[str, str]:
    effort = get_settings().ai_reasoning_effort
    return {"reasoning_effort": effort} if effort else {}
