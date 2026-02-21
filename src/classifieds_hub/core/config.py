from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
    )

    APP_ENV: str = "dev"
    LOG_LEVEL: str = "INFO"
    TZ: str = "Asia/Yakutsk"

    DATABASE_URL: str = "sqlite+aiosqlite:///data/classifieds.sqlite3"

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_DIGEST_CHAT_ID: int | None = None

    SOURCE_AYKHAL_ENABLED: bool = True
    TARGET_CITIES: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Aykhal", "Udachny"]
    )
    RUN_HOURS_LOCAL: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["09:00", "14:00", "18:00", "22:00"]
    )

    REQUEST_TIMEOUT_SECONDS: int = 20
    REQUEST_RETRIES: int = 2
    EXISTING_BACKFILL_PER_RUN: int = 10

    @field_validator("TARGET_CITIES", mode="before")
    @classmethod
    def parse_cities(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("RUN_HOURS_LOCAL", mode="before")
    @classmethod
    def parse_run_hours(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value
