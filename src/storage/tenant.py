"""Workspace-scoped DB context helpers."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


def set_workspace_context(session: Session, workspace_id: Optional[str]) -> None:
    """Set workspace context for PostgreSQL RLS policies."""

    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return

    value = workspace_id or ""
    session.execute(
        text("SELECT set_config('app.current_workspace_id', :workspace_id, true)"),
        {"workspace_id": value},
    )


def reset_workspace_context(session: Session) -> None:
    set_workspace_context(session=session, workspace_id=None)
