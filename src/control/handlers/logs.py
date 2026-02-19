"""Admin logs handler."""

from __future__ import annotations

from datetime import timezone
from typing import TYPE_CHECKING

from sqlalchemy import desc, select

from src.control.command_schema import ControlResponse
from src.storage.models import AdminAction

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def handle(context: "CommandContext") -> ControlResponse:
    limit = 5
    rows = context.session.scalars(
        select(AdminAction)
        .where(AdminAction.workspace_id == context.envelope.workspace_id)
        .order_by(desc(AdminAction.created_at))
        .limit(limit)
    ).all()

    payload = []
    for row in rows:
        created_at = row.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        payload.append(
            {
                "id": row.id,
                "command": row.command,
                "status": row.status,
                "result_summary": row.result_summary,
                "error": row.error_message,
                "created_at": created_at.isoformat(),
            }
        )

    return ControlResponse(
        success=True,
        message="logs_ok",
        data={"workspace_id": context.envelope.workspace_id, "items": payload},
    )
