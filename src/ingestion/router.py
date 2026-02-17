"""Ingestion API routes (read-only)."""

from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.auth.dependencies import require_workspace_role
from src.auth.jwt import AuthContext
from src.ingestion.open_calls import list_candidates, run_open_calls_ingestion
from src.integrations.x.x_client import XClient, get_x_client
from src.schemas.ingestion import (
    CandidateListResponse,
    CandidateResponse,
    OpenCallsRunRequest,
    OpenCallsRunResponse,
)
from src.storage.db import get_session
from src.storage.tenant import set_workspace_context


router = APIRouter(prefix="/ingestion", tags=["ingestion"])


def _enforce_workspace_scope(auth: AuthContext, workspace_id: str) -> None:
    if auth.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token workspace scope mismatch",
        )


@router.post("/open-calls/run", response_model=OpenCallsRunResponse)
def run_open_calls(
    payload: OpenCallsRunRequest,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
    x_client: XClient = Depends(get_x_client),
) -> OpenCallsRunResponse:
    _enforce_workspace_scope(auth, payload.workspace_id)
    set_workspace_context(session, payload.workspace_id)
    try:
        result = run_open_calls_ingestion(
            session,
            workspace_id=payload.workspace_id,
            x_client=x_client,
            max_results=payload.max_results,
            query=payload.query,
        )
    except RuntimeError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return OpenCallsRunResponse(
        workspace_id=payload.workspace_id,
        fetched=result.fetched,
        stored_new=result.stored_new,
        stored_updated=result.stored_updated,
        ranked=result.ranked,
        top_opportunity_score=result.top_opportunity_score,
    )


@router.get("/candidates/{workspace_id}", response_model=CandidateListResponse)
def get_candidates(
    workspace_id: str,
    limit: int = 20,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
) -> CandidateListResponse:
    _enforce_workspace_scope(auth, workspace_id)
    set_workspace_context(session, workspace_id)
    rows = list_candidates(session, workspace_id=workspace_id, limit=limit)

    response_rows: List[CandidateResponse] = []
    for row in rows:
        created_at = row.created_at.isoformat() if isinstance(row.created_at, datetime) else str(row.created_at)
        response_rows.append(
            CandidateResponse(
                id=row.id,
                workspace_id=row.workspace_id,
                source_tweet_id=row.source_tweet_id,
                author_handle=row.author_handle,
                text=row.text,
                intent=row.intent,
                opportunity_score=row.opportunity_score,
                url=row.url,
                created_at=created_at,
            )
        )

    return CandidateListResponse(
        workspace_id=workspace_id,
        count=len(response_rows),
        candidates=response_rows,
    )

