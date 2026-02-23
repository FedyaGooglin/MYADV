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
    RUN_ONCE_SEND_DELIVERY: bool = True

    TG_API_ID: int | None = None
    TG_API_HASH: str = ""
    TG_SESSION_NAME: str = "classifieds_hub_session"
    TG_SOURCE_CHATS: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["uda4niy"])
    TG_BACKFILL_DAYS: int = 30
    TG_FETCH_LIMIT_PER_RUN: int = 30
    TG_MAX_MESSAGES_PER_RUN: int = 300
    TG_DELAY_SECONDS: float = 1.2
    TG_STRICT_CLASSIFICATION: bool = True

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

    @field_validator("TG_SOURCE_CHATS", mode="before")
    @classmethod
    def parse_tg_chats(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip().lstrip("@") for item in value.split(",") if item.strip()]
        return [item.strip().lstrip("@") for item in value if item.strip()]
