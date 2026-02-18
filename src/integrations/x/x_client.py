"""HTTP client for X OAuth and read-only search ingestion."""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from src.core.config import get_settings


class XClientError(RuntimeError):
    """Raised when X API request fails."""


class XClient:
    def __init__(
        self,
        *,
        token_url: str,
        search_url: str,
        publish_url: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        timeout_seconds: int = 20,
        default_open_calls_query: str = "",
    ) -> None:
        self.token_url = token_url
        self.search_url = search_url
        self.publish_url = publish_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.timeout_seconds = timeout_seconds
        self.default_open_calls_query = default_open_calls_query

    def exchange_code_for_tokens(
        self,
        *,
        authorization_code: str,
        code_verifier: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.client_id:
            raise XClientError("X_CLIENT_ID is not configured")
        if not self.redirect_uri:
            raise XClientError("X_REDIRECT_URI is not configured")

        data: Dict[str, str] = {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier

        auth = (self.client_id, self.client_secret) if self.client_secret else None
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                self.token_url,
                data=data,
                auth=auth,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code >= 400:
            raise XClientError(f"X token exchange failed with status {response.status_code}")
        payload = response.json()
        if "access_token" not in payload:
            raise XClientError("X token exchange response missing access_token")
        return payload

    def search_open_calls(
        self,
        *,
        access_token: str,
        query: Optional[str] = None,
        max_results: int = 20,
    ) -> Dict[str, Any]:
        if not access_token:
            raise XClientError("Missing access token for X search")

        safe_max_results = max(10, min(max_results, 100))
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                self.search_url,
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "query": query or self.default_open_calls_query,
                    "max_results": safe_max_results,
                    "tweet.fields": "author_id,conversation_id,created_at,public_metrics,lang",
                    "expansions": "author_id",
                    "user.fields": "username,name",
                },
            )
        if response.status_code >= 400:
            raise XClientError(f"X search failed with status {response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise XClientError("Invalid X search payload format")
        return payload

    def create_tweet(
        self,
        *,
        access_token: str,
        text: str,
        in_reply_to_tweet_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not access_token:
            raise XClientError("Missing access token for X publish")
        if not text.strip():
            raise XClientError("Tweet text is required")

        payload: Dict[str, Any] = {"text": text}
        if in_reply_to_tweet_id:
            payload["reply"] = {"in_reply_to_tweet_id": in_reply_to_tweet_id}

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                self.publish_url,
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
        if response.status_code >= 400:
            raise XClientError(f"X publish failed with status {response.status_code}")

        parsed = response.json()
        if not isinstance(parsed, dict):
            raise XClientError("Invalid X publish payload format")
        return parsed


def get_x_client() -> XClient:
    settings = get_settings()
    return XClient(
        token_url=settings.x_token_url,
        search_url=settings.x_search_url,
        publish_url=settings.x_publish_url,
        client_id=settings.x_client_id,
        client_secret=settings.x_client_secret,
        redirect_uri=settings.x_redirect_uri,
        timeout_seconds=settings.x_api_timeout_seconds,
        default_open_calls_query=settings.x_default_open_calls_query,
    )
