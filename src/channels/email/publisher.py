"""Email publisher backed by provider integration."""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

from src.channels.base import ChannelPayload, ChannelPublishResult
from src.core.config import get_settings
from src.integrations.email import EmailClientError, ResendClient, get_resend_client


class EmailPublisher:
    channel = "email"

    def __init__(
        self,
        *,
        resend_client: Optional[ResendClient] = None,
        from_address: Optional[str] = None,
        default_recipients: Optional[Iterable[str]] = None,
    ) -> None:
        settings = get_settings()
        self._resend_client = resend_client
        self._from_address = (from_address or settings.email_from_address).strip()
        self._default_recipients = self._normalize_recipients(
            default_recipients
            if default_recipients is not None
            else settings.email_default_recipients.split(",")
        )

    @staticmethod
    def _normalize_recipients(raw_values: Iterable[Any]) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for raw in raw_values:
            value = str(raw).strip()
            if not value:
                continue
            lowered = value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(value)
        return normalized

    def _resolve_recipients(self, payload: ChannelPayload) -> List[str]:
        metadata = payload.metadata or {}
        recipients_field = metadata.get("to", metadata.get("recipients"))
        if isinstance(recipients_field, str):
            candidates = recipients_field.split(",")
        elif isinstance(recipients_field, list):
            candidates = recipients_field
        else:
            candidates = []

        recipients = self._normalize_recipients(candidates)
        if recipients:
            return recipients
        return list(self._default_recipients)

    def _resolve_client(self) -> ResendClient:
        if self._resend_client is not None:
            return self._resend_client
        return get_resend_client()

    def publish(self, payload: ChannelPayload) -> ChannelPublishResult:
        recipients = self._resolve_recipients(payload)
        if not recipients:
            return ChannelPublishResult(
                channel=self.channel,
                published=False,
                status="failed",
                message="email_recipients_missing",
                payload={"subject": payload.title, "body": payload.body},
            )

        subject = (payload.title or "RevFirst update").strip()
        if not subject:
            subject = "RevFirst update"

        tags = {"workspace_id": payload.workspace_id, "channel": self.channel}
        if payload.metadata.get("source_kind"):
            tags["source_kind"] = str(payload.metadata["source_kind"])

        try:
            response = self._resolve_client().send_email(
                from_address=self._from_address,
                to=recipients,
                subject=subject,
                text=payload.body,
                tags=tags,
            )
            message_id = response.get("id")
            external_id = str(message_id) if message_id else None
            return ChannelPublishResult(
                channel=self.channel,
                published=True,
                status="published",
                message="Email published",
                external_id=external_id,
                payload=response,
            )
        except EmailClientError as exc:
            return ChannelPublishResult(
                channel=self.channel,
                published=False,
                status="failed",
                message=str(exc),
                payload={
                    "subject": subject,
                    "recipients": recipients,
                },
            )
