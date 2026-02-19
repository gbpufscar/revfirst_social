"""Resend API client for workspace email publishing."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional

import httpx

from src.core.config import get_settings


class EmailClientError(RuntimeError):
    """Raised when email provider operations fail."""


class ResendClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.resend.com",
        timeout_seconds: int = 20,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = max(1, timeout_seconds)
        self._client = client

    def _headers(self) -> Dict[str, str]:
        if not self._api_key:
            raise EmailClientError("email_api_key_missing")
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def send_email(
        self,
        *,
        from_address: str,
        to: List[str],
        subject: str,
        text: str,
        tags: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        if not to:
            raise EmailClientError("email_recipients_missing")
        if not from_address.strip():
            raise EmailClientError("email_from_address_missing")
        if not subject.strip():
            raise EmailClientError("email_subject_missing")
        if not text.strip():
            raise EmailClientError("email_body_missing")

        payload: Dict[str, Any] = {
            "from": from_address.strip(),
            "to": [recipient.strip() for recipient in to if recipient.strip()],
            "subject": subject.strip(),
            "text": text.strip(),
        }
        if tags:
            payload["tags"] = [
                {"name": str(key).strip(), "value": str(value).strip()}
                for key, value in tags.items()
                if str(key).strip()
            ]

        if self._client is not None:
            response = self._client.post(
                f"{self._base_url}/emails",
                headers=self._headers(),
                json=payload,
            )
        else:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(
                    f"{self._base_url}/emails",
                    headers=self._headers(),
                    json=payload,
                )

        if response.status_code < 200 or response.status_code >= 300:
            detail = response.text.strip()
            if len(detail) > 200:
                detail = detail[:200] + "..."
            raise EmailClientError(
                f"email_provider_request_failed status={response.status_code} detail={detail}"
            )

        try:
            body = response.json()
        except Exception as exc:  # pragma: no cover
            raise EmailClientError("email_provider_invalid_json_response") from exc

        if not isinstance(body, dict):
            raise EmailClientError("email_provider_invalid_payload")
        return body


@lru_cache(maxsize=1)
def get_resend_client() -> ResendClient:
    settings = get_settings()
    return ResendClient(
        api_key=settings.email_api_key,
        base_url=settings.email_api_base_url,
        timeout_seconds=settings.email_api_timeout_seconds,
    )
