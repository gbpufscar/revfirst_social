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
        authorize_url: str,
        search_url: str,
        publish_url: str,
        users_me_url: str,
        user_lookup_url: str,
        user_tweets_url: str,
        tweet_lookup_url: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        timeout_seconds: int = 20,
        default_open_calls_query: str = "",
    ) -> None:
        self.token_url = token_url
        self.authorize_url = authorize_url
        self.search_url = search_url
        self.publish_url = publish_url
        self.users_me_url = users_me_url
        self.user_lookup_url = user_lookup_url
        self.user_tweets_url = user_tweets_url
        self.tweet_lookup_url = tweet_lookup_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.timeout_seconds = timeout_seconds
        self.default_open_calls_query = default_open_calls_query

    def _safe_json(self, response: httpx.Response, *, context: str) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise XClientError(f"{context} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise XClientError(f"{context} returned invalid payload format")
        return payload

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
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    self.token_url,
                    data=data,
                    auth=auth,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise XClientError("X token exchange request failed") from exc
        if response.status_code >= 400:
            raise XClientError(f"X token exchange failed with status {response.status_code}")
        payload = self._safe_json(response, context="X token exchange")
        if "access_token" not in payload:
            raise XClientError("X token exchange response missing access_token")
        return payload

    def refresh_access_token(
        self,
        *,
        refresh_token: str,
    ) -> Dict[str, Any]:
        if not self.client_id:
            raise XClientError("X_CLIENT_ID is not configured")
        if not refresh_token.strip():
            raise XClientError("Refresh token is required")

        data: Dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        auth = (self.client_id, self.client_secret) if self.client_secret else None
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    self.token_url,
                    data=data,
                    auth=auth,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise XClientError("X token refresh request failed") from exc
        if response.status_code >= 400:
            raise XClientError(f"X token refresh failed with status {response.status_code}")

        payload = self._safe_json(response, context="X token refresh")
        if "access_token" not in payload:
            raise XClientError("X token refresh response missing access_token")
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
        try:
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
        except httpx.HTTPError as exc:
            raise XClientError("X search request failed") from exc
        if response.status_code >= 400:
            raise XClientError(f"X search failed with status {response.status_code}")

        return self._safe_json(response, context="X search")

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

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    self.publish_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise XClientError("X publish request failed") from exc
        if response.status_code >= 400:
            raise XClientError(f"X publish failed with status {response.status_code}")

        return self._safe_json(response, context="X publish")

    def get_authenticated_user(
        self,
        *,
        access_token: str,
    ) -> Dict[str, Any]:
        if not access_token:
            raise XClientError("Missing access token for X users/me")

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(
                    self.users_me_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"user.fields": "username,name"},
                )
        except httpx.HTTPError as exc:
            raise XClientError("X users/me request failed") from exc
        if response.status_code >= 400:
            raise XClientError(f"X users/me failed with status {response.status_code}")
        payload = self._safe_json(response, context="X users/me")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise XClientError("X users/me response missing data")
        user_id = data.get("id")
        username = data.get("username")
        if not isinstance(user_id, str) or not user_id.strip():
            raise XClientError("X users/me response missing user id")
        if not isinstance(username, str) or not username.strip():
            raise XClientError("X users/me response missing username")
        return {"id": user_id.strip(), "username": username.strip()}

    def _format_lookup_url(self, template: str, placeholder: str, value: str) -> str:
        if not template.strip():
            raise XClientError("X URL template is not configured")
        if f"{{{placeholder}}}" in template:
            return template.format(**{placeholder: value})
        normalized = template.rstrip("/")
        return f"{normalized}/{value}"

    def _extract_has_image(self, payload: Dict[str, Any]) -> bool:
        data = payload.get("data")
        includes = payload.get("includes")
        if not isinstance(data, dict) or not isinstance(includes, dict):
            return False
        attachments = data.get("attachments")
        media_keys = []
        if isinstance(attachments, dict):
            raw_media_keys = attachments.get("media_keys")
            if isinstance(raw_media_keys, list):
                media_keys = [str(value) for value in raw_media_keys if str(value).strip()]
        if not media_keys:
            return False
        media_items = includes.get("media")
        if not isinstance(media_items, list):
            return False
        for item in media_items:
            if not isinstance(item, dict):
                continue
            media_key = str(item.get("media_key") or "").strip()
            if media_key not in media_keys:
                continue
            media_type = str(item.get("type") or "").strip().lower()
            if media_type in {"photo", "video", "animated_gif"}:
                return True
        return False

    def get_user_public_metrics(
        self,
        *,
        access_token: str,
        user_id: str,
    ) -> Dict[str, Any]:
        if not access_token:
            raise XClientError("Missing access token for X user lookup")
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise XClientError("User ID is required for X user lookup")

        url = self._format_lookup_url(self.user_lookup_url, "user_id", normalized_user_id)
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"user.fields": "username,name,public_metrics"},
                )
        except httpx.HTTPError as exc:
            raise XClientError("X user lookup request failed") from exc
        if response.status_code >= 400:
            raise XClientError(f"X user lookup failed with status {response.status_code}")

        payload = self._safe_json(response, context="X user lookup")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise XClientError("X user lookup response missing data")
        return data

    def get_user_recent_posts(
        self,
        *,
        access_token: str,
        user_id: str,
        max_results: int = 20,
    ) -> list[Dict[str, Any]]:
        if not access_token:
            raise XClientError("Missing access token for X user tweets")
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise XClientError("User ID is required for X user tweets")

        safe_max_results = max(5, min(max_results, 100))
        url = self._format_lookup_url(self.user_tweets_url, "user_id", normalized_user_id)
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={
                        "max_results": safe_max_results,
                        "exclude": "retweets",
                        "tweet.fields": "created_at,public_metrics,attachments,lang",
                        "expansions": "attachments.media_keys",
                        "media.fields": "type,url,preview_image_url",
                    },
                )
        except httpx.HTTPError as exc:
            raise XClientError("X user tweets request failed") from exc
        if response.status_code >= 400:
            raise XClientError(f"X user tweets failed with status {response.status_code}")

        payload = self._safe_json(response, context="X user tweets")
        rows = payload.get("data")
        if not isinstance(rows, list):
            return []

        includes = payload.get("includes")
        data_by_id: Dict[str, Dict[str, Any]] = {}
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("id"):
                    data_by_id[str(row["id"])] = row

        output: list[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            post_id = str(row.get("id") or "").strip()
            text = str(row.get("text") or "").strip()
            if not post_id or not text:
                continue

            wrapper_payload: Dict[str, Any] = {"data": row}
            if isinstance(includes, dict):
                wrapper_payload["includes"] = includes
            output.append(
                {
                    "id": post_id,
                    "text": text,
                    "created_at": row.get("created_at"),
                    "public_metrics": row.get("public_metrics") if isinstance(row.get("public_metrics"), dict) else {},
                    "has_image": self._extract_has_image(wrapper_payload),
                    "raw": row,
                }
            )
        return output

    def get_tweet_public_metrics(
        self,
        *,
        access_token: str,
        tweet_id: str,
    ) -> Dict[str, Any]:
        if not access_token:
            raise XClientError("Missing access token for X tweet lookup")
        normalized_tweet_id = tweet_id.strip()
        if not normalized_tweet_id:
            raise XClientError("Tweet ID is required for X tweet lookup")

        url = self._format_lookup_url(self.tweet_lookup_url, "tweet_id", normalized_tweet_id)
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={
                        "tweet.fields": "created_at,public_metrics,attachments,lang",
                        "expansions": "attachments.media_keys",
                        "media.fields": "type,url,preview_image_url",
                    },
                )
        except httpx.HTTPError as exc:
            raise XClientError("X tweet lookup request failed") from exc
        if response.status_code >= 400:
            raise XClientError(f"X tweet lookup failed with status {response.status_code}")

        payload = self._safe_json(response, context="X tweet lookup")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise XClientError("X tweet lookup response missing data")

        public_metrics = data.get("public_metrics")
        return {
            "id": str(data.get("id") or normalized_tweet_id),
            "created_at": data.get("created_at"),
            "public_metrics": public_metrics if isinstance(public_metrics, dict) else {},
            "has_image": self._extract_has_image(payload),
        }


def get_x_client() -> XClient:
    settings = get_settings()
    return XClient(
        token_url=settings.x_token_url,
        authorize_url=settings.x_authorize_url,
        search_url=settings.x_search_url,
        publish_url=settings.x_publish_url,
        users_me_url=settings.x_users_me_url,
        user_lookup_url=settings.x_user_lookup_url,
        user_tweets_url=settings.x_user_tweets_url,
        tweet_lookup_url=settings.x_tweet_lookup_url,
        client_id=settings.x_client_id,
        client_secret=settings.x_client_secret,
        redirect_uri=settings.x_redirect_uri,
        timeout_seconds=settings.x_api_timeout_seconds,
        default_open_calls_query=settings.x_default_open_calls_query,
    )
