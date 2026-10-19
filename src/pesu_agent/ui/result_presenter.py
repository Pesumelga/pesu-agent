"""Product UI Result Presenter & Sanitizing Adapter (Phase 10.4).

Translates domain InvestigationResult into safe, bounded, typed UI payloads.

Invariants:
1. Untrusted Content Hard Gate:
   - MODEL_TEXT_EXECUTABLE_AS_HTML = False
   - SLACK_TEXT_EXECUTABLE_AS_HTML = False
2. Bounded Payload Limits:
   - MAX_EVIDENCE_CARDS = 5
   - MAX_SNIPPET_LENGTH = 300
   - MAX_ANSWER_LENGTH = 4000
3. Safe Permalink Contract:
   - HTTPS_REQUIRED = True
   - SLACK_PERMALINK_REQUIRED = True
   - ARBITRARY_URL_OPEN_SUPPORTED = False
4. Request Isolation:
   - STALE_RESULT_CAN_OVERWRITE_CURRENT_QUERY = False
5. Zero Traceback or API Key Leakage to UI:
   - Typed error mapping converts technical errors into safe user-friendly Korean messages.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from pesu_agent.investigation.orchestrator import InvestigationResult
from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink

logger = logging.getLogger(__name__)

MAX_EVIDENCE_CARDS: int = 5
MAX_SNIPPET_LENGTH: int = 300
MAX_ANSWER_LENGTH: int = 4000

HTTPS_REQUIRED: bool = True
SLACK_PERMALINK_REQUIRED: bool = True
ARBITRARY_URL_OPEN_SUPPORTED: bool = False

MODEL_TEXT_EXECUTABLE_AS_HTML: bool = False
SLACK_TEXT_EXECUTABLE_AS_HTML: bool = False
STALE_RESULT_CAN_OVERWRITE_CURRENT_QUERY: bool = False

ERROR_MAPPINGS: dict[str, str] = {
    "LLM_BUDGET_REACHED": "이번 조사의 AI 분석 한도에 도달해 중단했습니다.",
    "EXTERNAL_ANALYSIS_CONSENT_REQUIRED": "Slack 근거를 외부 AI로 분석하려면 동의가 필요합니다.",
    "PROVIDER_EXECUTION_FAILURE": "AI 분석 호출에 실패했습니다. 근거가 없다는 뜻은 아닙니다.",
    "INVALID_PROVIDER_OUTPUT": "AI 분석 결과를 검증하지 못해 안전하게 중단했습니다.",
    "TRACEABILITY_FAILURE": "원문 출처 연결을 검증하지 못해 답변을 중단했습니다.",
    "AUTH_REQUIRED": "Slack 로그인이 필요합니다.",
    "SEARCH_UNAVAILABLE": "Slack 검색 기능에 접근할 수 없습니다.",
    "UI_CHANGED": "Slack 검색 기능에 접근할 수 없습니다.",
    "HOST_INIT_FAILED": "PESU 전용 Slack 런타임을 시작하지 못했습니다.",
    "MODEL_PROVIDER_FAILED": "AI 모델 호출에 실패했습니다.",
    "INVESTIGATION_FAILED": "조사 중 오류가 발생했습니다.",
    "BUSY": "현재 다른 조사를 진행 중입니다.",
}


def validate_citation_permalink(permalink: Optional[str]) -> bool:
    """Validates that a URL is an HTTPS canonical Slack message permalink."""
    if not permalink or not isinstance(permalink, str):
        return False

    trimmed = permalink.strip()
    if not trimmed.startswith("https://"):
        return False

    try:
        identity = parse_slack_message_permalink(trimmed)
        return identity is not None and bool(identity.channel_id) and bool(identity.message_ts)
    except Exception:
        return False


def format_user_friendly_error(error: Exception | str) -> str:
    """Maps technical errors or exceptions to clean, user-friendly strings without leaking stack traces or keys."""
    raw_str = str(error) if error else ""

    # Check for known error tokens
    for token, friendly_msg in ERROR_MAPPINGS.items():
        if token in raw_str:
            return friendly_msg

    # Common semantic patterns
    lower = raw_str.lower()
    if "auth" in lower or "login" in lower:
        return ERROR_MAPPINGS["AUTH_REQUIRED"]
    if "busy" in lower:
        return ERROR_MAPPINGS["BUSY"]
    if "rate limit" in lower or "quota" in lower or "openrouter" in lower:
        return ERROR_MAPPINGS["MODEL_PROVIDER_FAILED"]
    if "search" in lower or "webview" in lower:
        return ERROR_MAPPINGS["SEARCH_UNAVAILABLE"]

    # Fallback to safe default
    return ERROR_MAPPINGS["INVESTIGATION_FAILED"]


def present_investigation_result(result: InvestigationResult) -> dict[str, Any]:
    """Transforms domain InvestigationResult into bounded, safe UI presentation payload."""
    # 1. Answer assembly
    raw_answer_parts: list[str] = []
    if result.analysis and result.analysis.summary and result.analysis.summary.strip():
        raw_answer_parts.append(result.analysis.summary.strip())

    if result.analysis and result.analysis.confirmed_facts:
        facts_text = "\n".join(
            f"• {fact.statement.strip()}"
            for fact in result.analysis.confirmed_facts
            if fact.statement and fact.statement.strip()
        )
        if facts_text:
            raw_answer_parts.append(f"[확인된 사실]\n{facts_text}")

    if result.analysis and result.analysis.assessment and result.analysis.assessment.strip():
        raw_answer_parts.append(f"[종합 평가]\n{result.analysis.assessment.strip()}")

    full_answer = "\n\n".join(raw_answer_parts) if raw_answer_parts else ""
    if len(full_answer) > MAX_ANSWER_LENGTH:
        full_answer = full_answer[:MAX_ANSWER_LENGTH] + "\n...(답변 내용 일부 생략)"

    # 2. Evidence cards assembly
    evidence_cards: list[dict[str, Any]] = []
    if result.evidence_package and result.evidence_package.evidence_items:
        items = result.evidence_package.evidence_items[:MAX_EVIDENCE_CARDS]
        for item in items:
            raw_text = item.text or ""
            if len(raw_text) > MAX_SNIPPET_LENGTH:
                snippet = raw_text[:MAX_SNIPPET_LENGTH].rstrip() + "..."
            else:
                snippet = raw_text

            # Check permalink validity
            raw_permalink = getattr(item, "permalink", None) or getattr(item, "message_identifier", None)
            is_valid_url = validate_citation_permalink(raw_permalink)
            canonical_url = raw_permalink if is_valid_url else None

            # Subcitation replies
            replies_payload = []
            for r in getattr(item, "thread_replies", []):
                r_valid_url = validate_citation_permalink(r.permalink)
                r_text = r.text or ""
                r_snip = r_text[:MAX_SNIPPET_LENGTH].rstrip() + ("..." if len(r_text) > MAX_SNIPPET_LENGTH else "")
                replies_payload.append({
                    "reply_id": f"{item.evidence_id}-{r.reply_id}",
                    "author": str(r.author or "알 수 없음"),
                    "timestamp": str(r.timestamp or ""),
                    "snippet": r_snip,
                    "permalink": r.permalink if r_valid_url else None,
                    "has_valid_permalink": r_valid_url,
                })

            evidence_cards.append({
                "id": str(item.evidence_id),
                "source": "Slack" if (item.source or "").lower().startswith("slack") else (item.source or "출처"),
                "author": str(item.author or "알 수 없음"),
                "timestamp": str(item.timestamp or ""),
                "channel": str(item.channel or ""),
                "snippet": snippet,
                "permalink": canonical_url,
                "has_valid_permalink": is_valid_url,
                "thread_replies": replies_payload,
                "thread_context_status": getattr(item, "thread_context_status", "NOT_ATTEMPTED"),
            })

    has_evidence = len(evidence_cards) > 0

    return {
        "investigation_id": result.investigation_id,
        "answer": full_answer,
        "has_evidence": has_evidence,
        "evidence_count": len(evidence_cards),
        "evidence": evidence_cards,
        "no_evidence_message": (
            None
            if has_evidence
            else "관련 근거를 찾지 못했습니다.\n질문을 조금 더 구체적으로 입력해 보세요."
        ),
        "stop_reason": result.stop_reason.value if hasattr(result.stop_reason, "value") else str(result.stop_reason),
        "retrieval_coverage": result.evidence_package.retrieval_coverage if result.evidence_package else None,
        "thread_context_status": result.evidence_package.thread_context_status if result.evidence_package else None,
    }


def present_grounded_investigation_result(
    investigation_id: str,
    payload: Any,
    stop_reason: Optional[str] = None,
) -> dict[str, Any]:
    """Transforms GroundedWorkItemAnswerPayload into WinForms UI presentation schema."""
    # 1. Answer assembly & length bounding
    full_answer = (getattr(payload, "formatted_markdown_answer", "") or "").strip()
    if len(full_answer) > MAX_ANSWER_LENGTH:
        full_answer = full_answer[:MAX_ANSWER_LENGTH] + "\n...(답변 내용 일부 생략)"

    # 2. Evidence cards assembly
    evidence_cards: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for card in getattr(payload, "work_item_cards", []):
        for cit in getattr(card, "evidence_citations", []):
            eid = cit.get("evidence_id")
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                raw_text = cit.get("snippet") or ""
                if len(raw_text) > MAX_SNIPPET_LENGTH:
                    snippet = raw_text[:MAX_SNIPPET_LENGTH].rstrip() + "..."
                else:
                    snippet = raw_text

                raw_permalink = cit.get("permalink")
                is_valid_url = validate_citation_permalink(raw_permalink)
                canonical_url = raw_permalink if is_valid_url else None

                evidence_cards.append({
                    "id": str(eid),
                    "source": "Slack",
                    "author": str(cit.get("author") or "알 수 없음"),
                    "timestamp": str(cit.get("timestamp") or ""),
                    "channel": str(cit.get("channel") or ""),
                    "snippet": snippet,
                    "permalink": canonical_url,
                    "has_valid_permalink": is_valid_url,
                    "thread_replies": [],
                    "thread_context_status": "COMPLETED",
                })

    bounded_cards = evidence_cards[:MAX_EVIDENCE_CARDS]
    visible_ids = {item["id"] for item in bounded_cards}
    source_work_items = getattr(payload, "work_item_cards", [])
    content_truncated = (
        len(source_work_items) > 10 or len(evidence_cards) > MAX_EVIDENCE_CARDS
        or len(getattr(payload, "formatted_markdown_answer", "") or "") > MAX_ANSWER_LENGTH
        or any(len(card.title) > MAX_SNIPPET_LENGTH
               or len(card.decisive_claims) > 5 or len(card.blocker_details) > 5
               or any(len(text) > MAX_SNIPPET_LENGTH for text in card.decisive_claims + card.blocker_details)
               for card in source_work_items)
    )
    work_items = [
        {
            "id": card.work_item_id,
            "title": card.title[:MAX_SNIPPET_LENGTH],
            "status": card.status,
            "status_label": card.status_label_ko,
            "decisive_claims": [text[:MAX_SNIPPET_LENGTH] for text in card.decisive_claims[:5]],
            "findings": [{"label": row["label"], "statement": row["statement"][:MAX_SNIPPET_LENGTH]} for row in card.findings[:5]],
            "blocker_details": [text[:MAX_SNIPPET_LENGTH] for text in card.blocker_details[:5]],
            "evidence_ids": list(dict.fromkeys(
                cit["evidence_id"] for cit in card.evidence_citations
                if cit.get("evidence_id") in visible_ids
            )),
            "omitted_evidence_count": len({
                cit["evidence_id"] for cit in card.evidence_citations
                if cit.get("evidence_id") and cit["evidence_id"] not in visible_ids
            }),
        }
        for card in getattr(payload, "work_item_cards", [])[:10]
    ]
    has_evidence = len(bounded_cards) > 0
    resolved_stop_reason = stop_reason or getattr(payload, "stop_reason", "COMPLETED")

    no_evidence_msg = None
    if not has_evidence:
        no_evidence_msg = (
            getattr(payload, "uncertainty_explanation", None)
            or "관련 근거를 찾지 못했습니다.\n질문을 조금 더 구체적으로 입력해 보세요."
        )

    return {
        "investigation_id": investigation_id,
        "answer": full_answer,
        "summary_headline": str(getattr(payload, "summary_headline", ""))[:MAX_SNIPPET_LENGTH],
        "is_conclusive": bool(getattr(payload, "is_conclusive", False)),
        "uncertainty_explanation": str(getattr(payload, "uncertainty_explanation", "") or "")[:MAX_ANSWER_LENGTH],
        "work_items": work_items,
        "content_truncated": content_truncated,
        "has_evidence": has_evidence,
        "evidence_count": getattr(payload, "evidence_count", len(evidence_cards)),
        "evidence": bounded_cards,
        "no_evidence_message": no_evidence_msg,
        "stop_reason": str(resolved_stop_reason),
        "retrieval_coverage": None,
        "thread_context_status": "COMPLETED" if has_evidence else None,
    }
