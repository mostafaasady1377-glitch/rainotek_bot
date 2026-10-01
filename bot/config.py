from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str = ""
    BOT_USERNAME: str = "rainotek_bot"
    ADMIN_TELEGRAM_IDS: list[int] = Field(default_factory=list)
    DATABASE_URL: str = "sqlite+aiosqlite:///./rainotek.db"
    TZ: str = "Asia/Tehran"
    STORE_WARRANTY: str = "۲ ماه ضمانت تست و تعویض راینوتک"
    SHEET_PRICE_MULTIPLIER: int = 1000
    GOOGLE_SHEET_ID: str = ""
    GOOGLE_SERVICE_ACCOUNT_FILE: str = ""
    GROQ_API_KEY: str = ""
    GROQ_STT_MODEL: str = "whisper-large-v3-turbo"
    GROQ_LLM_MODEL: str = "llama-3.3-70b-versatile"

    @field_validator("ADMIN_TELEGRAM_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> list[int]:
        if value in (None, ""):
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [int(item) for item in value]
        return [int(value)]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
