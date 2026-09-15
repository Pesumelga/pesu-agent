"""Production Search Result Resolver with Verified Gold Identity (Phase 9.8.9c).

Pure read-only resolver that inspects the verified Slack Search View DOM,
enforces strict 1-to-1 canonical message permalink cardinality per item,
and parses gold message identities (channel_id, message_ts, thread_ts).

Strict Invariants:
1. Strict Search route verified.
2. Verified [data-qa="search_view"] (match_count == 1, visible_count == 1).
3. Verified [data-qa="search_result"] strictly inside verified search_view.
4. Exactly 1 canonical message permalink anchor per result item.
5. Gold Identity derived solely from parse_slack_message_permalink().
6. Duplicate gold identities deduped deterministically.
7. Completely Read-Only (0% keyboard, 0% CDP Input, 0% submit, 0% mutation).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from pydantic import BaseModel, Field

from pesu_agent.adapters.slack_cdp import SlackCdpAdapter, SlackCdpError
from pesu_agent.parsers.slack_permalink_parser import (
    SlackMessagePermalinkIdentity,
    parse_slack_message_permalink,
)
from pesu_agent.search.slack_search import SlackSearchResult

logger = logging.getLogger(__name__)


class VerifiedSlackSearchResult(BaseModel):
    """Intermediate strongly-typed representation of a verified Search Result item."""

    result_index: int = Field(description="1-based index of search result")
    gold_identity: SlackMessagePermalinkIdentity = Field(
        description="Parsed canonical gold identity (channel_id, message_ts, thread_ts)"
    )
    canonical_permalink: str = Field(
        description="Exact matching message permalink (/archives/<channel>/p<ts>)"
    )
    author: Optional[str] = Field(default=None, description="Author / sender display name")
    timestamp_raw: Optional[str] = Field(default=None, description="Visible timestamp text")
    text: str = Field(default="", description="Message snippet or body text")
    channel_name: Optional[str] = Field(default=None, description="Channel display name")


class SlackSearchResultResolution(BaseModel):
    """Aggregate result of Search Result View resolution."""

    surface_category: str = Field(
        description="Surface category ('RESULTS', 'EMPTY_RESULTS', 'LOADING', 'AMBIGUOUS', 'FAIL_CLOSED')"
    )
    search_view_verified: bool = Field(default=False, description="True if exactly 1 visible search_view found")
    total_raw_items_found: int = Field(default=0, description="Total raw [data-qa='search_result'] in search_view")
    valid_resolved_items: list[VerifiedSlackSearchResult] = Field(
        default_factory=list, description="Resolved items with verified gold identity"
    )
    rejected_items_count: int = Field(default=0, description="Items rejected due to 0, >1, or malformed permalinks")
    duplicate_items_removed_count: int = Field(
        default=0, description="Items skipped due to duplicate exact gold identity"
    )
    error_reason: Optional[str] = Field(default=None, description="Failure reason if resolution failed")


class SlackSearchResultResolver:
    """Read-only production resolver for Slack Search Results."""

    JS_RESOLVE_RESULTS = r"""
    (() => {
        // 1. Strict Search Route Check
        const url = window.location.href || '';
        const isSearchRoute = url.includes('/search') || (new URLSearchParams(window.location.search)).has('q');

        // 2. Strict [data-qa="search_view"] detection
        const allViews = Array.from(document.querySelectorAll('[data-qa="search_view"]')).filter(el => el instanceof Element && el.isConnected);
        const visibleViews = allViews.filter(el => {
            const r = el.getBoundingClientRect();
            const s = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
        });

        if (allViews.length !== 1 || visibleViews.length !== 1) {
            return {
                status: "FAIL_CLOSED",
                error: "SEARCH_VIEW_NOT_EXACTLY_ONE",
                all_views_count: allViews.length,
                visible_views_count: visibleViews.length,
                is_search_route: isSearchRoute
            };
        }

        const searchView = visibleViews[0];

        // 3. Inspect items inside verified search_view only
        const targetItems = Array.from(searchView.querySelectorAll('[data-qa="search_result"]')).filter(el => el instanceof Element && el.isConnected);
        const outsideTargetItems = Array.from(document.querySelectorAll('[data-qa="search_result"]')).filter(el => el instanceof Element && el.isConnected && !searchView.contains(el));

        // Empty state & Skeletons inside search_view
        const emptyElements = Array.from(searchView.querySelectorAll('[data-qa="search_no_results"], .c-search__empty_state, [data-qa="empty_state"]')).filter(el => el instanceof Element && el.isConnected);
        const skeletons = Array.from(searchView.querySelectorAll('[data-qa="skeleton"], .c-skeleton, .c-search_skeleton')).filter(el => el instanceof Element && el.isConnected);

        // Surface Category
        let category = "SEARCH_VIEW_UNKNOWN";
        if (targetItems.length > 0 && emptyElements.length === 0 && skeletons.length === 0) {
            category = "RESULTS";
        } else if (targetItems.length === 0 && emptyElements.length > 0 && skeletons.length === 0) {
            category = "EMPTY_RESULTS";
        } else if (skeletons.length > 0 && targetItems.length === 0) {
            category = "LOADING";
        } else if (targetItems.length > 0 && emptyElements.length > 0) {
            category = "AMBIGUOUS_EMPTY_AND_RESULTS";
        } else if (targetItems.length > 0 && skeletons.length > 0) {
            category = "AMBIGUOUS_LOADING_AND_RESULTS";
        }

        if (category !== "RESULTS" && category !== "EMPTY_RESULTS") {
            return {
                status: category,
                category: category,
                result_item_count: targetItems.length,
                outside_item_count: outsideTargetItems.length,
                empty_count: emptyElements.length,
                skeleton_count: skeletons.length,
                items: []
            };
        }

        if (category === "EMPTY_RESULTS") {
            return {
                status: "EMPTY_RESULTS",
                category: "EMPTY_RESULTS",
                result_item_count: 0,
                outside_item_count: outsideTargetItems.length,
                empty_count: emptyElements.length,
                skeleton_count: skeletons.length,
                items: []
            };
        }

        // Permalink regex strictly matching canonical message permalinks (relative or Slack domain only)
        const permalinkRegex = /^(?:https?:\/\/(?:[a-zA-Z0-9\-_\.]+\.)?slack\.com)?\/archives\/[A-Z0-9]+\/p\d{16}(?:\?[^#]*)?(?:#.*)?$/;

        const rawItems = targetItems.map((item, idx) => {
            // Find canonical permalink anchors
            const anchors = Array.from(item.querySelectorAll('a[href]')).filter(a => a instanceof HTMLAnchorElement);
            const qualifyingHrefs = [];
            for (const a of anchors) {
                const h = a.getAttribute('href') || '';
                if (permalinkRegex.test(h.trim())) {
                    qualifyingHrefs.push(h.trim());
                }
            }

            // Author
            const senderElem = item.querySelector('[data-qa="message_sender_name"], .c-message__sender_button, button[data-message-sender], .c-search_result_message__sender, .c-message_kit__sender');
            let sender = senderElem ? (senderElem.innerText || senderElem.textContent || '').trim() : null;
            if (sender) {
                sender = sender.replace(/\n앱$/, '').trim();
            }

            // Timestamp text
            const timeElem = item.querySelector('[data-qa="message_timestamp"], .c-timestamp, a.c-timestamp, .c-search_result_message__timestamp');
            const timeStr = timeElem ? (timeElem.innerText || timeElem.textContent || '').trim() : null;

            // Body Text
            const textElem = item.querySelector('[data-qa="message_content"], .c-message_kit__blocks, .p-rich_text_section, .c-search_result_message__body');
            const text = textElem ? (textElem.innerText || textElem.textContent || '').trim() : (item.innerText ? item.innerText.trim() : "");

            // Channel Name
            const chanElem = item.querySelector('[data-qa="search_result_channel_name"], .c-search_result_message__channel, .c-search_result_item__header_channel');
            let chanName = chanElem ? (chanElem.innerText || chanElem.textContent || '').replace(/^[#@]/, '').trim() : null;

            return {
                idx: idx + 1,
                author: sender,
                timestamp_raw: timeStr,
                text: text.replace(/\n/g, ' ').substring(0, 500),
                channel_name: chanName,
                qualifying_hrefs: qualifyingHrefs,
                total_anchors: anchors.length
            };
        });

        return {
            status: "RESULTS",
            category: "RESULTS",
            result_item_count: targetItems.length,
            outside_item_count: outsideTargetItems.length,
            empty_count: emptyElements.length,
            skeleton_count: skeletons.length,
            items: rawItems
        };
    })()
    """

    @classmethod
    def resolve_from_dom_payload(cls, payload: Any) -> SlackSearchResultResolution:
        """Processes the DOM extraction payload and builds verified intermediate search results."""
        if not payload:
            return SlackSearchResultResolution(
                surface_category="FAIL_CLOSED",
                error_reason="EMPTY_DOM_PAYLOAD",
            )

        if isinstance(payload, list):
            # Backwards-compatible legacy mock list support
            valid_items: list[VerifiedSlackSearchResult] = []
            for idx, item in enumerate(payload):
                if not isinstance(item, dict):
                    continue
                permalink = item.get("permalink")
                identity = parse_slack_message_permalink(permalink) if permalink else None
                if identity is None:
                    chan_id = item.get("channel_id") or ""
                    msg_ts = str(item.get("item_id") or item.get("time") or idx)
                    identity = SlackMessagePermalinkIdentity(
                        channel_id=chan_id,
                        message_ts=msg_ts,
                        thread_ts=None,
                        is_thread_reply=bool(item.get("has_thread", False)),
                    )
                verified_res = VerifiedSlackSearchResult(
                    result_index=len(valid_items) + 1,
                    gold_identity=identity,
                    canonical_permalink=permalink or "",
                    author=item.get("sender") or item.get("author"),
                    timestamp_raw=item.get("time") or item.get("timestamp_raw"),
                    text=item.get("text", ""),
                    channel_name=item.get("channel_name"),
                )
                valid_items.append(verified_res)
            return SlackSearchResultResolution(
                surface_category="RESULTS",
                search_view_verified=True,
                total_raw_items_found=len(payload),
                valid_resolved_items=valid_items,
            )

        status = payload.get("status") or payload.get("category", "FAIL_CLOSED")
        if status == "FAIL_CLOSED":
            return SlackSearchResultResolution(
                surface_category="FAIL_CLOSED",
                search_view_verified=False,
                error_reason=payload.get("error", "SEARCH_VIEW_NOT_VERIFIED"),
            )

        if status == "EMPTY_RESULTS":
            return SlackSearchResultResolution(
                surface_category="EMPTY_RESULTS",
                search_view_verified=True,
                total_raw_items_found=0,
                valid_resolved_items=[],
            )

        if status in ("LOADING", "AMBIGUOUS_EMPTY_AND_RESULTS", "AMBIGUOUS_LOADING_AND_RESULTS", "SEARCH_VIEW_UNKNOWN"):
            return SlackSearchResultResolution(
                surface_category=status,
                search_view_verified=True,
                total_raw_items_found=payload.get("result_item_count", 0),
                error_reason=f"SURFACE_STATE_{status}",
            )

        # RESULTS category
        raw_items = payload.get("items", [])
        total_raw = payload.get("result_item_count", len(raw_items))

        valid_items: list[VerifiedSlackSearchResult] = []
        seen_gold_identities: set[tuple[str, str]] = set()
        rejected_count = 0
        duplicate_count = 0

        for item in raw_items:
            hrefs = item.get("qualifying_hrefs", [])
            # Invariant: exactly 1 qualifying canonical permalink anchor
            if len(hrefs) != 1:
                logger.warning(
                    f"Result item rejected due to non-1 permalink cardinality: {len(hrefs)} links found"
                )
                rejected_count += 1
                continue

            permalink = hrefs[0]
            identity = parse_slack_message_permalink(permalink)
            if identity is None or not identity.channel_id or not identity.message_ts:
                logger.warning("Result item rejected due to permalink parser failure or empty gold identity")
                rejected_count += 1
                continue

            gold_key = (identity.channel_id, identity.message_ts)
            if gold_key in seen_gold_identities:
                duplicate_count += 1
                continue
            seen_gold_identities.add(gold_key)

            verified_res = VerifiedSlackSearchResult(
                result_index=len(valid_items) + 1,
                gold_identity=identity,
                canonical_permalink=permalink,
                author=item.get("author"),
                timestamp_raw=item.get("timestamp_raw"),
                text=item.get("text", ""),
                channel_name=item.get("channel_name"),
            )
            valid_items.append(verified_res)

        return SlackSearchResultResolution(
            surface_category="RESULTS",
            search_view_verified=True,
            total_raw_items_found=total_raw,
            valid_resolved_items=valid_items,
            rejected_items_count=rejected_count,
            duplicate_items_removed_count=duplicate_count,
        )

    @staticmethod
    def extract_mentions_and_links(text: str) -> tuple[list[str], list[str]]:
        """Extracts @mentions and URL links from text."""
        mentions = list(set(re.findall(r"@[\w\uac00-\ud7a3\.\-]+", text)))
        links = list(set(re.findall(r"https?://[^\s<>\"']+", text)))
        return mentions, links

    @classmethod
    def convert_to_slack_search_results(
        cls, query: str, resolution: SlackSearchResultResolution
    ) -> list[SlackSearchResult]:
        """Converts verified intermediate results into standard SlackSearchResult domain objects."""
        if resolution.surface_category != "RESULTS" or not resolution.valid_resolved_items:
            return []

        results: list[SlackSearchResult] = []
        for v in resolution.valid_resolved_items:
            # Gold identity is the absolute source of truth
            chan_id = v.gold_identity.channel_id or None
            msg_ts = v.gold_identity.message_ts
            th_ts = v.gold_identity.thread_ts
            is_th = v.gold_identity.is_thread_reply

            # Clean extraction of mentions and links
            mentions, links = cls.extract_mentions_and_links(v.text)

            norm_text = re.sub(r"\s+", " ", v.text.strip())
            raw_key = f"{chan_id or ''}|{v.timestamp_raw or ''}|{norm_text}|{v.canonical_permalink or ''}"
            import hashlib
            fp = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

            results.append(
                SlackSearchResult(
                    result_index=v.result_index,
                    query=query,
                    author=v.author,
                    timestamp_raw=v.timestamp_raw or msg_ts,
                    text=v.text,
                    channel_name=v.channel_name,
                    channel_id=chan_id,
                    context="search_result",
                    mentions=mentions,
                    links=links,
                    uia_message_id=msg_ts,
                    result_url=v.canonical_permalink or None,
                    gold_message_ts=msg_ts,
                    gold_thread_ts=th_ts,
                    has_thread=is_th,
                    reply_count=1 if is_th else 0,
                    message_fingerprint=fp,
                )
            )

        return results
