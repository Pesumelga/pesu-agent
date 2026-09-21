"""Canonical Slack Message Permalink Identity Parser (Phase 9.8.9b.3).

Pure function parser that translates canonical Slack message permalinks into structured
gold message identity (channel_id, message_ts, thread_ts, is_thread_reply).

Invariants:
- Zero raw href, raw channel ID, or raw message timestamp logging.
- Strict canonical permalink regex: /archives/<conversation_id>/p<16 digits>
- Microsecond timestamp canonicalization: p<10s><6us> -> seconds.microseconds
- Precise thread reply semantics: thread_ts from query param preserved separately from message_ts.
- Rejection of generic channel links, profile links, search links, and malformed timestamps.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Optional
from pydantic import BaseModel, Field


class SlackMessagePermalinkIdentity(BaseModel):
    """Canonical Slack Message Identity parsed from a message permalink."""

    channel_id: str = Field(
        description="Canonical Slack conversation or channel ID (e.g. 'C0123456789', 'D0123456789')"
    )
    message_ts: str = Field(
        description="Canonical message timestamp in Slack API format 'seconds.microseconds' (e.g. '1724738400.123456')"
    )
    thread_ts: Optional[str] = Field(
        default=None,
        description="Root thread timestamp if this message is a thread reply (e.g. '1724738000.000100')",
    )
    is_thread_reply: bool = Field(
        default=False,
        description="True if permalink represents a reply inside a thread",
    )


# Regex strictly matching canonical Slack message permalink paths (relative or Slack domain only)
_CANONICAL_MESSAGE_PERMALINK_RE = re.compile(
    r"^(?:https?://(?:[a-zA-Z0-9\-_\.]+\.)?slack\.com)?/archives/(?P<channel_id>[A-Z0-9]+)/p(?P<raw_ts>\d{16})(?:\?(?P<query>[^#]*))?(?:#.*)?$"
)


def parse_slack_message_permalink(href: str) -> Optional[SlackMessagePermalinkIdentity]:
    """Pure function parsing a canonical Slack message permalink into structured gold identity.

    Zero raw href, channel, or timestamp values are logged.

    Args:
        href: Raw anchor href attribute (e.g. '/archives/C0123/p1724738400123456?thread_ts=1724738000.000100')

    Returns:
        SlackMessagePermalinkIdentity if canonical message permalink, else None.
    """
    if not href or not isinstance(href, str):
        return None

    m = _CANONICAL_MESSAGE_PERMALINK_RE.match(href.strip())
    if not m:
        return None

    channel_id = m.group("channel_id")
    raw_ts = m.group("raw_ts")
    query_str = m.group("query") or ""

    # Canonicalize p<16 digits> -> seconds.microseconds
    # First 10 digits are unix epoch seconds, remaining 6 digits are microseconds
    seconds = raw_ts[:10]
    microseconds = raw_ts[10:]
    message_ts = f"{seconds}.{microseconds}"

    thread_ts: Optional[str] = None
    is_thread_reply = False

    if query_str:
        params = urllib.parse.parse_qs(query_str)
        if "thread_ts" in params and params["thread_ts"]:
            raw_thread_val = params["thread_ts"][0]
            if re.match(r"^\d+\.\d+$", raw_thread_val):
                thread_ts = raw_thread_val
                is_thread_reply = True

    return SlackMessagePermalinkIdentity(
        channel_id=channel_id,
        message_ts=message_ts,
        thread_ts=thread_ts,
        is_thread_reply=is_thread_reply,
    )
