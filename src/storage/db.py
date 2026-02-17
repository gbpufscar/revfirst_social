"""SQLAlchemy engine/session primitives and health checks."""

from __future__ import annotations

from functools import lru_cache
from typing import Generator, Optional, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from src.core.config import get_settings


Base = declarative_base()


@lru_cache(maxsize=1)
def get_engine():
    settings = get_settings()
    kwargs: dict[str, object] = {"pool_pre_ping": True, "future": True}

    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}

    return create_engine(settings.database_url, **kwargs)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def test_connection() -> Tuple[bool, Optional[str]]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # pragma: no cover
        return False, str(exc)


def load_models() -> None:
    """Import ORM models so Base metadata contains all mapped tables."""

    # Import side effect is intentional here.
    import src.storage.models  # noqa: F401
