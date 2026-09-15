"""Slack Real-Time Search (RTS) API SearchBackend Implementation (Phase 9.6).

This module implements the SearchBackend Protocol using Slack's official
assistant.search.context (Real-Time Search API).

Strict Safety & Compliance Invariants:
1. Zero Persistence Contract:
   - Raw API response payloads are strictly processed in-memory.
   - No message content, raw payload, or EvidenceItem is written to persistent disk storage.
   - Benchmark artifacts only store aggregate non-content metrics.
2. Call Budget Enforcement:
   - Max RTS calls per investigation default <= 6 (hard cap < 10).
   - Pagination consumes from the call budget.
3. HTTP 429 Rate Limit Handling:
   - Respects Retry-After header with bounded backoff (max 2 retries, no infinite loops).
4. Token Management:
   - Supports both static (xoxp-...) and rotating (xoxe.xoxp-... + xoxe-...) tokens based on OAuth response fields.
5. In-Memory EvidenceItem Mapping:
   - Primary identifiers: (channel_id, message_ts) and permalink.
   - Nearby context preserved in-memory via context_messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional, Protocol, Union
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from pydantic import BaseModel, Field

from pesu_agent.investigation.models import EvidenceItem

logger = logging.getLogger(__name__)

MAX_RTS_CALLS_DEFAULT = 6
MAX_RTS_CALLS_HARD_LIMIT = 10
MAX_429_RETRIES = 2


# ==============================================================================
# RTS Response Models (Official data.results.messages[] Schema)
# ==============================================================================

class RtsContextMessages(BaseModel):
    """Nearby context messages before and after a matched message."""
    before: list[dict[str, Any]] = Field(default_factory=list)
    after: list[dict[str, Any]] = Field(default_factory=list)


class RtsMessageItem(BaseModel):
    """Single message item in assistant.search.context results."""
    channel_id: str
    channel_name: Optional[str] = None
    message_ts: str
    content: str = ""
    permalink: Optional[str] = None
    context_messages: Optional[RtsContextMessages] = None


class RtsFileItem(BaseModel):
    """Single file item in assistant.search.context results."""
    id: str
    name: Optional[str] = None
    permalink: Optional[str] = None
    mimetype: Optional[str] = None


class RtsResults(BaseModel):
    """Container for search results returned by assistant.search.context."""
    messages: list[RtsMessageItem] = Field(default_factory=list)
    files: list[RtsFileItem] = Field(default_factory=list)
    channels: list[dict[str, Any]] = Field(default_factory=list)


class RtsResponseMetadata(BaseModel):
    """Cursor pagination metadata."""
    next_cursor: Optional[str] = None


class RtsSearchResponse(BaseModel):
    """Top-level response from assistant.search.context."""
    ok: bool
    results: Optional[RtsResults] = None
    error: Optional[str] = None
    response_metadata: Optional[RtsResponseMetadata] = None


# ==============================================================================
# Token Provider Protocol & Implementations
# ==============================================================================

class SlackTokenProvider(Protocol):
    """Protocol for providing valid Slack OAuth User Tokens."""
    async def get_access_token(self) -> str:
        """Returns a valid access token string."""
        ...


class StaticSlackTokenProvider:
    """Token provider for non-rotating static user tokens (xoxp-...)."""

    def __init__(self, token: str):
        if not token or not token.strip():
            raise ValueError("Slack user token cannot be empty.")
        self._token = token.strip()

    async def get_access_token(self) -> str:
        return self._token


class RotatingSlackTokenProvider:
    """Token provider for rotating access tokens (xoxe.xoxp-... + refresh token xoxe-...).
    
    Determines rotation state based on expires_in / expires_at fields, not token prefix.
    """

    def __init__(
        self,
        client_id: str,
        initial_access_token: str,
        initial_refresh_token: str,
        client_secret: Optional[str] = None,
        expires_in_sec: int = 43200,
        token_endpoint: str = "https://slack.com/api/oauth.v2.access",
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = initial_access_token
        self._refresh_token = initial_refresh_token
        self._expires_at = time.time() + max(60, expires_in_sec - 300)
        self._token_endpoint = token_endpoint
        self._lock = asyncio.Lock()

    @property
    def is_expired(self) -> bool:
        return time.time() >= self._expires_at

    async def get_access_token(self) -> str:
        async with self._lock:
            if self.is_expired:
                await self._refresh_access_token()
            return self._access_token

    async def _refresh_access_token(self) -> None:
        """Refreshes the access token using grant_type=refresh_token."""
        payload: dict[str, str] = {
            "client_id": self._client_id,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        if self._client_secret:
            payload["client_secret"] = self._client_secret

        data = urllib_parse.urlencode(payload).encode("utf-8")

        req = urllib_request.Request(
            self._token_endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        loop = asyncio.get_event_loop()
        try:
            resp_body = await loop.run_in_executor(None, lambda: urllib_request.urlopen(req, timeout=10.0).read())
            resp_json = json.loads(resp_body.decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"Slack OAuth token refresh HTTP request failed: {e}") from e

        if not resp_json.get("ok"):
            raise RuntimeError(f"Slack OAuth token refresh rejected: {resp_json.get('error')}")

        new_access = resp_json.get("access_token")
        new_refresh = resp_json.get("refresh_token")
        expires_in = resp_json.get("expires_in", 43200)

        if not new_access:
            raise RuntimeError("Slack token refresh response missing access_token.")

        self._access_token = new_access
        if new_refresh:
            self._refresh_token = new_refresh
        self._expires_at = time.time() + max(60, expires_in - 300)
        logger.info("Slack OAuth access token successfully rotated.")


# ==============================================================================
# Slack RTS SearchBackend
# ==============================================================================

class SlackRtsSearchBackend:
    """Production SearchBackend implementation using assistant.search.context.
    
    Adheres strictly to the in-memory Zero Persistence and Call Budget contracts.
    """

    def __init__(
        self,
        token_provider: SlackTokenProvider,
        max_call_budget: int = MAX_RTS_CALLS_DEFAULT,
        api_base_url: str = "https://slack.com/api",
        custom_http_client: Optional[Any] = None,
    ):
        self.token_provider = token_provider
        self.max_call_budget = min(max_call_budget, MAX_RTS_CALLS_HARD_LIMIT)
        self.api_base_url = api_base_url.rstrip("/")
        self._custom_http_client = custom_http_client

    async def search(self, query: str, limit: int = 20) -> list[EvidenceItem]:
        """Performs workspace-wide search via assistant.search.context.
        
        Maps results into in-memory EvidenceItems with composite (channel_id, message_ts) identities.
        """
        if not query or not query.strip():
            return []

        call_count = 0
        cursor: Optional[str] = None
        all_items: list[EvidenceItem] = []
        seen_identities: set[tuple[str, str]] = set()

        while call_count < self.max_call_budget and len(all_items) < limit:
            call_count += 1
            payload: dict[str, Any] = {
                "query": query.strip(),
                "types": ["messages", "files"],
                "limit": min(20, limit - len(all_items)),
            }
            if cursor:
                payload["cursor"] = cursor

            resp_data = await self._execute_rts_call_with_retry(payload)
            if not resp_data.ok or not resp_data.results:
                break

            # Process in-memory messages
            for msg in resp_data.results.messages:
                ident = (msg.channel_id, msg.message_ts)
                if ident in seen_identities:
                    continue
                seen_identities.add(ident)

                canonical_id = msg.permalink or msg.message_ts
                channel_repr = f"{msg.channel_name} ({msg.channel_id})" if msg.channel_name else msg.channel_id

                item = EvidenceItem(
                    evidence_id=f"slack_msg_{msg.message_ts}",
                    source="slack",
                    author=None,
                    timestamp=msg.message_ts,
                    text=msg.content,
                    channel=channel_repr,
                    message_identifier=canonical_id,
                    thread_identifier=None,
                    has_attachments=False,
                    attachment_count=0,
                    attachment_names=[],
                )
                all_items.append(item)
                if len(all_items) >= limit:
                    break

            # Check pagination cursor
            if resp_data.response_metadata and resp_data.response_metadata.next_cursor:
                cursor = resp_data.response_metadata.next_cursor.strip()
                if not cursor:
                    break
            else:
                break

        return all_items

    async def _execute_rts_call_with_retry(self, payload: dict[str, Any]) -> RtsSearchResponse:
        """Executes a single assistant.search.context call with bounded 429 Retry-After handling."""
        url = f"{self.api_base_url}/assistant.search.context"
        retries = 0

        while retries <= MAX_429_RETRIES:
            token = await self.token_provider.get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "PesuAgent-RTS/1.0",
            }
            data_bytes = json.dumps(payload).encode("utf-8")

            # Support mock / custom client for testing
            if self._custom_http_client:
                status_code, resp_headers, body_str = await self._custom_http_client.post(url, headers, data_bytes)
            else:
                req = urllib_request.Request(url, data=data_bytes, headers=headers, method="POST")
                loop = asyncio.get_event_loop()
                try:
                    def _do_req():
                        try:
                            with urllib_request.urlopen(req, timeout=10.0) as resp:
                                return resp.status, resp.headers, resp.read().decode("utf-8")
                        except urllib_error.HTTPError as err:
                            return err.code, err.headers, err.read().decode("utf-8")

                    status_code, resp_headers, body_str = await loop.run_in_executor(None, _do_req)
                except Exception as exc:
                    raise RuntimeError(f"Slack RTS HTTP connection failed: {exc}") from exc

            # Handle HTTP 429 Rate Limit
            if status_code == 429:
                retries += 1
                if retries > MAX_429_RETRIES:
                    raise RuntimeError("Slack RTS API rate limit exceeded (HTTP 429) after maximum retries.")

                retry_after_str = resp_headers.get("Retry-After", "1") if hasattr(resp_headers, "get") else "1"
                try:
                    retry_after = float(retry_after_str)
                except ValueError:
                    retry_after = 1.0

                sleep_time = min(max(1.0, retry_after), 5.0)
                logger.warning(f"Slack RTS HTTP 429 encountered. Backing off for {sleep_time}s (attempt {retries}/{MAX_429_RETRIES})")
                await asyncio.sleep(sleep_time)
                continue

            if status_code != 200:
                raise RuntimeError(f"Slack RTS HTTP error {status_code}: {body_str}")

            try:
                data = json.loads(body_str)
            except json.JSONDecodeError as err:
                raise RuntimeError(f"Failed to parse Slack RTS response JSON: {err}") from err

            parsed = RtsSearchResponse.model_validate(data)
            if not parsed.ok:
                raise RuntimeError(f"Slack RTS API error: {parsed.error}")

            return parsed

        raise RuntimeError("Slack RTS API call failed after retries.")
