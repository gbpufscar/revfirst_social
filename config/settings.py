"""Global settings loader for RevFirst_Social."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_env: str
    timezone: str
    x_api_key: str
    x_api_secret: str
    telegram_bot_token: str
    data_dir: str
    db_path: str


def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("REVFIRST_ENV", "development"),
        timezone=os.getenv("REVFIRST_TIMEZONE", "UTC"),
        x_api_key=os.getenv("X_API_KEY", ""),
        x_api_secret=os.getenv("X_API_SECRET", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        data_dir=os.getenv("REVFIRST_DATA_DIR", "data"),
        db_path=os.getenv("DB_PATH", "data/db.sqlite"),
    )
