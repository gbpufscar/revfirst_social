"""Control Plane module (Telegram command center)."""

from src.control.telegram_bot import router as control_telegram_router

__all__ = ["control_telegram_router"]
