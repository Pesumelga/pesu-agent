"""Unit and regression tests for Bounded Read-Only Slack Thread Context Expansion (Phase 12.3l).

Covers all 46 required test cases and regression invariants:
1. eligible thread candidate retained
2. root permalink alone does not infer a thread
3. current DOM resident row exact match -> eligible to click
4. candidate absent from current DOM -> click 0
5. duplicate/ambiguous resident rows -> click 0
6. reply_bar missing -> click 0
7. reply_bar count >1 -> click 0
8. reply_bar composer overlap -> click 0
9. preexisting dual roots + exactly one new root -> select new root
10. new root count 0 -> fail closed
11. new root count >1 -> fail closed
12. exact slack_kit_list cardinality 1 accepted
13. broad generic list alone rejected
14. composer virtual-list-item is excluded
15. ordinary message row accepted
16. pasted Slack archive link inside .c-message_kit__blocks rejected
17. exact a.c-timestamp outside body accepted
18. zero own timestamp -> row skipped
19. multiple own timestamps -> row skipped
20. malformed permalink -> row skipped
21. wrong channel -> row skipped
22. wrong thread_ts -> row skipped
23. root row message_ts == thread_ts excluded from replies
24. reply row message_ts != thread_ts accepted
25. one .p-rich_text_section body extracted
26. three sections aggregated in DOM order
27. empty body -> row skipped
28. no whole-row text fallback
29. author exact selector present -> author populated
30. author absent -> valid reply retained with author=None
31. timestamp optional remains None without qualified source
32. reply IDs contiguous over accepted replies only
33. max replies <= 10
34. total characters <= 3000
35. no automated scroll
36. no qualified completeness proof -> PARTIAL
37. legacy/generic header must not make COMPLETE
38. thread failure preserves parent evidence
39. no URL navigation fallback reachable
40. no generic keyboard/input/focus/blur capability in thread path
41. exact reply_bar click is at most once
42. root-delta timeout cleans PESU JS state
43. extraction failure cleans PESU JS state
44. next ThreadReader invocation starts without stale operation state
45. reply with missing thread_ts is rejected
46. selected resident row reference disconnected before click -> zero clicks
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from pesu_agent.history.store import InvestigationHistoryStore
from pesu_agent.investigation.analyzer import InvestigationAnalyzer
from pesu_agent.investigation.exceptions import EvidenceCitationError
from pesu_agent.investigation.models import (
    AnalysisResult,
    ConfirmedFact,
    EvidenceItem,
    EvidencePackage,
    InvestigationPlan,
    SearchQuery,
    ThreadContextStatus,
    ThreadReplyItem,
    UserQuestion,
)
from pesu_agent.investigation.orchestrator import (
    InvestigationOrchestrator,
    OrchestratorConfig,
)
from pesu_agent.investigation.search_backend import SyntheticSearchBackend
from pesu_agent.llm.models import LLMUsageStats
from pesu_agent.parsers.slack_permalink_parser import (
    SlackMessagePermalinkIdentity,
    parse_slack_message_permalink,
)
from pesu_agent.runtime.pesu_webview_host import (
    PesuWebViewHost,
    VerifiedThreadNavigationRequest,
)
from pesu_agent.search.webview_thread_reader import (
    COMPOSER_ROW_CAN_BECOME_THREAD_REPLY,
    JS_CLEANUP_THREAD_OP_STATE,
    JS_CLICK_RESIDENT_ROW_REPLY_BAR,
    JS_EXTRACT_MOUNTED_THREAD_REPLIES,
    JS_INSPECT_RESIDENT_SEARCH_ROWS,
    JS_POLL_ROOT_DELTA,
    MAX_ACCEPTED_REPLIES,
    MAX_FINAL_THREAD_CHARS,
    THREAD_READER_URL_NAVIGATION_FALLBACK,
    UNQUALIFIED_COMPLETENESS_PATH_DISABLED,
    WebviewThreadReader,
    ThreadReadResult,
    ThreadReadFailureReason,
    classify_message_thread_role,
    is_eligible_thread_candidate,
    normalize_slack_ts,
)


def create_mock_host(
    inspect_result: Any = None,
    click_result: Any = None,
    poll_results: Any = None,
    extract_result: Any = None,
) -> MagicMock:
    """Helper factory for a mock host with deterministic evaluate_js simulation."""
    mock_host = MagicMock()
    eval_calls = []
    poll_queue = list(poll_results or [{"ready": True, "new_root_count": 1}])

    default_inspect = {
        "ok": True,
        "rows": [
            {
                "row_index": 0,
                "permalinks": [
                    "https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001"
                ],
            }
        ],
    }

    default_click = {
        "ok": True,
        "guard_passed": True,
        "click_executed": True,
        "pre_click_root_count": 0,
    }

    default_extract = {
        "ok": True,
        "replies": [
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
                "text": "1차 간담회 일정 댓글입니다.",
                "author": "홍길동",
            }
        ],
    }

    def fake_evaluate_js(script: str, timeout: float = 5.0) -> Any:
        eval_calls.append(script)
        if "maxCandidateRows" in script or "slack_kit_list" in script:
            return extract_result if extract_result is not None else default_extract
        if "selectedRowIndex" in script or "replyBar.click()" in script:
            return click_result if click_result is not None else default_click
        if "newRoots = afterRoots.filter" in script or "new_root_count" in script:
            if poll_queue:
                return poll_queue.pop(0)
            return {"ready": True, "new_root_count": 1}
        if "searchViewSel" in script or "search_rows = resultRows" in script:
            return inspect_result if inspect_result is not None else default_inspect
        if "delete window.__pesu_thread_op" in script:
            return None
        return {"ok": True}

    mock_host.evaluate_js.side_effect = fake_evaluate_js
    mock_host.eval_calls = eval_calls
    return mock_host


def create_eligible_candidate(
    msg_id: str = "https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
    thr_id: str = "1787000000.000001",
    has_thread: bool = True,
    reply_count: int = 1,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id="E1",
        source="slack",
        text="26년 하반기 간담회 공지",
        message_identifier=msg_id,
        thread_identifier=thr_id,
        has_thread=has_thread,
        thread_reply_count=reply_count,
    )


# ---------------------------------------------------------------------------
# 1. Eligibility & Root Inference Invariants
# ---------------------------------------------------------------------------

def test_eligible_thread_candidate_retained():
    """Requirement 1: eligible thread candidate is retained and accepted."""
    cand = create_eligible_candidate()
    assert is_eligible_thread_candidate(cand) is True


def test_root_permalink_alone_does_not_infer_thread():
    """Requirement 2: root permalink alone without thread_ts or has_thread is rejected."""
    cand = EvidenceItem(
        evidence_id="E1",
        source="slack",
        text="일반 메시지",
        message_identifier="https://app.slack.com/archives/C01/p1787000000000001",
        thread_identifier=None,
        has_thread=False,
    )
    assert is_eligible_thread_candidate(cand) is False


# ---------------------------------------------------------------------------
# 2. Policy A: Search Row Residency Matching & Ambiguity Guards
# ---------------------------------------------------------------------------

def test_dom_resident_row_exact_match_eligible_to_click():
    """Requirement 3: exact matching resident search row proceeds to reply_bar click."""
    host = create_mock_host()
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert status == ThreadContextStatus.PARTIAL
    # Verify click script was evaluated
    assert any("selectedRowIndex" in c for c in host.eval_calls)


def test_candidate_absent_from_dom_zero_clicks():
    """Requirement 4: candidate absent from mounted search rows -> click 0, NOT_ATTEMPTED."""
    host = create_mock_host(inspect_result={"ok": True, "rows": []})
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 0
    assert status == ThreadContextStatus.NOT_ATTEMPTED
    # Ensure no replyBar click script was executed
    assert not any("selectedRowIndex" in c for c in host.eval_calls)


def test_duplicate_ambiguous_resident_rows_zero_clicks():
    """Requirement 5: multiple/ambiguous resident rows -> click 0, UNAVAILABLE."""
    ambiguous_inspect = {
        "ok": True,
        "rows": [
            {
                "row_index": 0,
                "permalinks": ["https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001"],
            },
            {
                "row_index": 1,
                "permalinks": ["https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001"],
            },
        ],
    }
    host = create_mock_host(inspect_result=ambiguous_inspect)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 0
    assert status == ThreadContextStatus.UNAVAILABLE
    assert not any("selectedRowIndex" in c for c in host.eval_calls)


# ---------------------------------------------------------------------------
# 3. Exact reply_bar Guards
# ---------------------------------------------------------------------------

def test_reply_bar_missing_zero_clicks():
    """Requirement 6: missing reply_bar in row -> click 0, UNAVAILABLE."""
    click_fail = {
        "ok": False,
        "guard_passed": False,
        "guard_failure_reason": "REPLY_BAR_COUNT_INVALID",
        "reply_bar_count": 0,
        "click_executed": False,
    }
    host = create_mock_host(click_result=click_fail)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 0
    assert status == ThreadContextStatus.UNAVAILABLE


def test_reply_bar_count_greater_than_one_zero_clicks():
    """Requirement 7: reply_bar count > 1 -> click 0, UNAVAILABLE."""
    click_fail = {
        "ok": False,
        "guard_passed": False,
        "guard_failure_reason": "REPLY_BAR_COUNT_INVALID",
        "reply_bar_count": 2,
        "click_executed": False,
    }
    host = create_mock_host(click_result=click_fail)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 0
    assert status == ThreadContextStatus.UNAVAILABLE


def test_reply_bar_composer_overlap_zero_clicks():
    """Requirement 8: reply_bar overlapping composer/contenteditable -> click 0, UNAVAILABLE."""
    click_fail = {
        "ok": False,
        "guard_passed": False,
        "guard_failure_reason": "REPLY_BAR_INSIDE_COMPOSER",
        "reply_bar_count": 1,
        "click_executed": False,
    }
    host = create_mock_host(click_result=click_fail)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 0
    assert status == ThreadContextStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# 4. Click-Correlated Root Delta Polling
# ---------------------------------------------------------------------------

def test_preexisting_dual_roots_and_one_new_root_selects_new():
    """Requirement 9: pre-existing roots + exactly 1 newly mounted root -> success."""
    poll_results = [{"ready": True, "new_root_count": 1, "post_click_count": 2, "pre_click_count": 1}]
    host = create_mock_host(poll_results=poll_results)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert status == ThreadContextStatus.PARTIAL


def test_new_root_count_zero_fails_closed():
    """Requirement 10: new root count 0 at timeout -> fail closed (UNAVAILABLE)."""
    poll_results = [{"ready": False, "new_root_count": 0, "post_click_count": 1, "pre_click_count": 1}]
    host = create_mock_host(poll_results=poll_results)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand, timeout=0.1)
    assert len(replies) == 0
    assert status == ThreadContextStatus.UNAVAILABLE


def test_new_root_count_greater_than_one_fails_closed():
    """Requirement 11: new root count > 1 -> fail closed (UNAVAILABLE)."""
    poll_results = [{"ready": False, "new_root_count": 2, "post_click_count": 3, "pre_click_count": 1}]
    host = create_mock_host(poll_results=poll_results)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand, timeout=0.1)
    assert len(replies) == 0
    assert status == ThreadContextStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# 5. Message Region & Virtual List Row Structural Guards
# ---------------------------------------------------------------------------

def test_exact_slack_kit_list_cardinality_one_accepted():
    """Requirement 12: exact slack_kit_list with cardinality 1 is accepted."""
    assert "data-qa=\"slack_kit_list\"" in JS_EXTRACT_MOUNTED_THREAD_REPLIES
    assert "messageRegions.length !== 1" in JS_EXTRACT_MOUNTED_THREAD_REPLIES


def test_broad_generic_list_alone_rejected():
    """Requirement 13: generic list without slack_kit_list is rejected."""
    extract_fail = {
        "ok": False,
        "error": "MESSAGE_REGION_NOT_FOUND",
        "message_region_count": 0,
    }
    host = create_mock_host(extract_result=extract_fail)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 0
    assert status == ThreadContextStatus.UNAVAILABLE


def test_composer_virtual_list_item_excluded():
    """Requirement 14: composer virtual-list-item is excluded from message rows."""
    assert "data-qa=\"message_input\"" in JS_EXTRACT_MOUNTED_THREAD_REPLIES
    assert "contenteditable" in JS_EXTRACT_MOUNTED_THREAD_REPLIES
    assert COMPOSER_ROW_CAN_BECOME_THREAD_REPLY is False


def test_ordinary_message_row_accepted():
    """Requirement 15: non-composer message row is processed."""
    host = create_mock_host()
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].text == "1차 간담회 일정 댓글입니다."


# ---------------------------------------------------------------------------
# 6. Timestamp Anchor & Body Pasted Link Isolation
# ---------------------------------------------------------------------------

def test_pasted_slack_archive_link_inside_blocks_rejected():
    """Requirement 16: pasted Slack archive link inside .c-message_kit__blocks is rejected."""
    # In JS script, insideBlocks check filters out in-body archive links
    assert "closest('.c-message_kit__blocks')" in JS_EXTRACT_MOUNTED_THREAD_REPLIES


def test_exact_timestamp_anchor_outside_body_accepted():
    """Requirement 17: exact a.c-timestamp outside body blocks is accepted."""
    assert "a.c-timestamp" in JS_EXTRACT_MOUNTED_THREAD_REPLIES


def test_zero_own_timestamp_row_skipped():
    """Requirement 18: row with zero own timestamp anchors is skipped without failing thread."""
    extract_with_skipped = {
        "ok": True,
        "replies": [
            # Row 1 skipped (0 anchors, not present in replies)
            # Row 2 accepted
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000002?thread_ts=1787000000.000001",
                "text": "두 번째 댓글",
                "author": "이몽룡",
            }
        ],
    }
    host = create_mock_host(extract_result=extract_with_skipped)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].reply_id == "R1"
    assert replies[0].text == "두 번째 댓글"


def test_multiple_own_timestamps_row_skipped():
    """Requirement 19: row with multiple own timestamp anchors (>1) is skipped."""
    assert "rawTsAnchors.length !== 1" in JS_EXTRACT_MOUNTED_THREAD_REPLIES


# ---------------------------------------------------------------------------
# 7. Canonical Permalink Validation & Row Skipping
# ---------------------------------------------------------------------------

def test_malformed_permalink_row_skipped():
    """Requirement 20: row with unparseable permalink is skipped."""
    extract_res = {
        "ok": True,
        "replies": [
            {"href": "https://app.slack.com/archives/invalid", "text": "무효 링크"},
            {"href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001", "text": "유효 댓글"},
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].text == "유효 댓글"


def test_wrong_channel_row_skipped():
    """Requirement 21: row from different channel is skipped."""
    extract_res = {
        "ok": True,
        "replies": [
            {"href": "https://app.slack.com/archives/CDIFFERENT/p1788000000000001?thread_ts=1787000000.000001", "text": "다른 채널"},
            {"href": "https://app.slack.com/archives/C01/p1788000000000002?thread_ts=1787000000.000001", "text": "올바른 채널"},
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].text == "올바른 채널"


def test_wrong_thread_ts_row_skipped():
    """Requirement 22: row belonging to different thread_ts is skipped."""
    extract_res = {
        "ok": True,
        "replies": [
            {"href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1789999999.000000", "text": "다른 스레드"},
            {"href": "https://app.slack.com/archives/C01/p1788000000000002?thread_ts=1787000000.000001", "text": "올바른 스레드"},
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].text == "올바른 스레드"


# ---------------------------------------------------------------------------
# 8. Strict Root vs Reply Identity Rule
# ---------------------------------------------------------------------------

def test_root_row_message_ts_equals_thread_ts_excluded():
    """Requirement 23: root message (message_ts == thread_ts) is excluded from replies."""
    extract_res = {
        "ok": True,
        "replies": [
            # Root row
            {"href": "https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001", "text": "루트 본문"},
            # Reply row
            {"href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001", "text": "스레드 댓글"},
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].text == "스레드 댓글"
    assert replies[0].reply_id == "R1"


def test_reply_row_message_ts_not_equals_thread_ts_accepted():
    """Requirement 24: reply message (message_ts != thread_ts and thread_ts == target) is accepted."""
    host = create_mock_host()
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].message_ts != replies[0].thread_ts


# ---------------------------------------------------------------------------
# 9. Scoped Body Extraction & Section Aggregation
# ---------------------------------------------------------------------------

def test_one_rich_text_section_body_extracted():
    """Requirement 25: single .p-rich_text_section text is cleanly extracted."""
    extract_res = {
        "ok": True,
        "replies": [
            {"href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001", "text": "단일 섹션 본문"},
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].text == "단일 섹션 본문"


def test_three_sections_aggregated_in_dom_order():
    """Requirement 26: three sections aggregated in DOM order."""
    assert "texts.join('\\n')" in JS_EXTRACT_MOUNTED_THREAD_REPLIES


def test_empty_body_row_skipped():
    """Requirement 27: empty body row is skipped."""
    extract_res = {
        "ok": True,
        "replies": [
            {"href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001", "text": "   "},
            {"href": "https://app.slack.com/archives/C01/p1788000000000002?thread_ts=1787000000.000001", "text": "실제 본문"},
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].text == "실제 본문"


def test_no_whole_row_text_fallback():
    """Requirement 28: whole-row .innerText fallback is absent."""
    assert "row.innerText" not in JS_EXTRACT_MOUNTED_THREAD_REPLIES
    assert "row.textContent" not in JS_EXTRACT_MOUNTED_THREAD_REPLIES


# ---------------------------------------------------------------------------
# 10. Author & Timestamp Optional Handling
# ---------------------------------------------------------------------------

def test_author_exact_selector_present_populated():
    """Requirement 29: [data-qa="message_sender_name"] exact selector populates author."""
    extract_res = {
        "ok": True,
        "replies": [
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
                "text": "작성자 확인",
                "author": "홍길동",
            }
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].author == "홍길동"


def test_author_absent_retained_as_none():
    """Requirement 30: author absent -> reply retained with author=None."""
    extract_res = {
        "ok": True,
        "replies": [
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
                "text": "익명 작성자",
                "author": None,
            }
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].author is None


def test_timestamp_optional_remains_none():
    """Requirement 31: timestamp optional remains None without qualified live source."""
    host = create_mock_host()
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert replies[0].timestamp is None


# ---------------------------------------------------------------------------
# 11. Contiguous IDs and Bounds
# ---------------------------------------------------------------------------

def test_reply_ids_contiguous_over_accepted_replies():
    """Requirement 32: accepted replies receive contiguous R1, R2, R3 IDs."""
    extract_res = {
        "ok": True,
        "replies": [
            {"href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001", "text": "첫번째"},
            {"href": "https://app.slack.com/archives/C01/p1788000000000002?thread_ts=1787000000.000001", "text": "두번째"},
            {"href": "https://app.slack.com/archives/C01/p1788000000000003?thread_ts=1787000000.000001", "text": "세번째"},
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert [r.reply_id for r in replies] == ["R1", "R2", "R3"]


def test_max_replies_budget_enforced():
    """Requirement 33: MAX_ACCEPTED_REPLIES <= 10 is enforced."""
    raw_list = [
        {
            "href": f"https://app.slack.com/archives/C01/p17880000000000{i:02d}?thread_ts=1787000000.000001",
            "text": f"댓글 {i}",
        }
        for i in range(1, 15)
    ]
    extract_res = {"ok": True, "replies": raw_list}
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host, max_replies=10)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 10
    assert replies[-1].reply_id == "R10"


def test_max_characters_budget_enforced():
    """Requirement 34: total characters budget (3000) truncates gracefully."""
    raw_list = [
        {
            "href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
            "text": "A" * 3500,
        }
    ]
    extract_res = {"ok": True, "replies": raw_list}
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host, max_context_chars=3000)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 1
    assert len(replies[0].text) <= 3000


def test_no_automated_scroll():
    """Requirement 35: automated scroll is false."""
    assert "scrollTo" not in JS_EXTRACT_MOUNTED_THREAD_REPLIES
    assert "scrollBy" not in JS_EXTRACT_MOUNTED_THREAD_REPLIES


# ---------------------------------------------------------------------------
# 12. Completeness Policy
# ---------------------------------------------------------------------------

def test_no_qualified_completeness_proof_returns_partial():
    """Requirement 36: without qualified completeness proof -> PARTIAL."""
    host = create_mock_host()
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) >= 1
    assert status == ThreadContextStatus.PARTIAL


def test_legacy_header_does_not_make_complete():
    """Requirement 37: legacy header selector does not produce COMPLETE."""
    assert UNQUALIFIED_COMPLETENESS_PATH_DISABLED is True
    assert "thread_replies_count" not in JS_EXTRACT_MOUNTED_THREAD_REPLIES


# ---------------------------------------------------------------------------
# 13. Failure Isolation & Safety Barriers
# ---------------------------------------------------------------------------

def test_thread_failure_preserves_parent_evidence():
    """Requirement 38: thread failure does not modify parent evidence item."""
    cand = create_eligible_candidate()
    host = create_mock_host(click_result={"ok": False, "guard_passed": False, "click_executed": False})
    reader = WebviewThreadReader(host=host)

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 0
    assert status == ThreadContextStatus.UNAVAILABLE
    assert cand.text == "26년 하반기 간담회 공지"


def test_no_url_navigation_fallback_reachable():
    """Requirement 39: no URL navigation fallback reachable from thread reader."""
    assert THREAD_READER_URL_NAVIGATION_FALLBACK is False
    host = create_mock_host()
    reader = WebviewThreadReader(host=host)

    assert not hasattr(reader, "navigate_thread_context")
    assert not hasattr(reader, "derive_internal_thread_route")
    assert not hasattr(reader, "derive_canonical_thread_permalink")


def test_no_generic_keyboard_input_focus_blur_in_thread_path():
    """Requirement 40: zero keyboard/input/focus/blur capability in thread reader."""
    all_js = (
        JS_CLEANUP_THREAD_OP_STATE
        + JS_INSPECT_RESIDENT_SEARCH_ROWS
        + JS_CLICK_RESIDENT_ROW_REPLY_BAR
        + JS_POLL_ROOT_DELTA
        + JS_EXTRACT_MOUNTED_THREAD_REPLIES
    )
    assert ".blur()" not in all_js
    assert ".focus()" not in all_js
    assert "dispatchEvent" not in all_js
    assert "KeyboardEvent" not in all_js
    assert "InputEvent" not in all_js


def test_exact_reply_bar_click_at_most_once():
    """Requirement 41: reply_bar click is executed at most once."""
    host = create_mock_host()
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    reader.read_thread_context(cand)
    click_calls = [c for c in host.eval_calls if "selectedRowIndex" in c]
    assert len(click_calls) == 1


# ---------------------------------------------------------------------------
# 14. Additional Cleanup & Strict Identity Regression Tests
# ---------------------------------------------------------------------------

def test_root_delta_timeout_cleans_pesu_js_state():
    """Requirement 42: root-delta timeout invokes terminal PESU JS state cleanup."""
    poll_timeout_result = [{"ready": False, "new_root_count": 0}]
    host = create_mock_host(poll_results=poll_timeout_result)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand, timeout=0.1)
    assert status == ThreadContextStatus.UNAVAILABLE
    # Check that cleanup script was evaluated
    assert any("delete window.__pesu_thread_op" in c for c in host.eval_calls)


def test_extraction_failure_cleans_pesu_js_state():
    """Requirement 43: extraction failure cleans PESU JS state via try/finally."""
    host = create_mock_host(extract_result={"ok": False, "error": "CORRUPT_DOM"})
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert status == ThreadContextStatus.UNAVAILABLE
    assert any("delete window.__pesu_thread_op" in c for c in host.eval_calls)


def test_next_thread_reader_invocation_starts_without_stale_operation_state():
    """Requirement 44: next invocation executes pre-sanitation before any DOM query."""
    host = create_mock_host()
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    reader.read_thread_context(cand)
    # The very first evaluate_js call must be the state cleanup script
    assert "delete window.__pesu_thread_op" in host.eval_calls[0]


def test_reply_with_missing_thread_ts_is_rejected():
    """Requirement 45: reply candidate with missing thread_ts is strictly rejected."""
    extract_res = {
        "ok": True,
        "replies": [
            # Missing thread_ts query parameter
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000001",
                "text": "부모 없는 메시지",
            }
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 0
    assert status == ThreadContextStatus.UNAVAILABLE


def test_selected_resident_row_reference_disconnected_before_click_zero_clicks():
    """Requirement 46: disconnected row reference before click results in 0 clicks and fail closed."""
    host = create_mock_host(
        click_result={"ok": False, "error": "TARGET_ROW_DISCONNECTED", "click_executed": False}
    )
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate()

    replies, status = reader.read_thread_context(cand)
    assert len(replies) == 0
    assert status == ThreadContextStatus.UNAVAILABLE
    assert reader.last_telemetry.get("click_executed") is False


# ---------------------------------------------------------------------------
# 15. Downstream Integration & Sub-citation Regression Invariants
# ---------------------------------------------------------------------------

def test_timestamp_normalization():
    """Verify Slack timestamp normalization handles microsecond precision."""
    assert normalize_slack_ts("1787709893.004509") == "1787709893.004509"
    assert normalize_slack_ts("1787709893.4509") == "1787709893.450900"
    assert normalize_slack_ts("1787709893") == "1787709893.000000"
    assert normalize_slack_ts(None) is None
    assert normalize_slack_ts("not_a_ts") is None


def test_classify_message_thread_role():
    """Verify Parent / Reply identity classification rules."""
    assert classify_message_thread_role("1787709893.004509", None) == "STANDALONE_OR_ROOT"
    assert classify_message_thread_role("1787709893.004509", "") == "STANDALONE_OR_ROOT"
    assert classify_message_thread_role("1787709893.004509", "1787709893.004509") == "THREAD_ROOT"
    assert classify_message_thread_role("1788152491.960529", "1787709893.004509") == "THREAD_REPLY"
    assert classify_message_thread_role(None, "1787709893.004509") == "AMBIGUOUS"
    assert classify_message_thread_role("invalid", "1787709893.004509") == "AMBIGUOUS"


def test_generic_navigate_api_not_exposed():
    """Verify GENERIC_NAVIGATE_URL_API_EXPOSED = false on PesuWebViewHost."""
    host = PesuWebViewHost(initial_url="about:blank")
    assert not hasattr(host, "navigate"), "Generic navigate(url: str) must NOT be exposed."
    assert hasattr(host, "navigate_thread_context"), "Only narrow typed capability must be exposed."


def test_wildcard_slack_origin_rejected():
    """Verify WILDCARD_SLACK_ORIGIN_ALLOWED = false."""
    host = PesuWebViewHost(initial_url="about:blank")
    host._ready_event.set()
    host.get_runtime_qualified_origins = MagicMock(return_value={"https://app.slack.com", "https://authorized.slack.com"})

    attack_req = VerifiedThreadNavigationRequest(
        channel_id="C0123",
        canonical_permalink="https://evil-unauthorized.slack.com/archives/C0123/p1787709893004509",
        expected_root_ts="1787709893.004509",
    )

    with patch.object(host, "_form", create=True) as mock_form:
        mock_form.InvokeRequired = False
        with patch.object(host, "_webview", create=True):
            assert host.navigate_thread_context(attack_req) is False


def test_analyzer_subcitation_validation():
    """Verify exact sub-citations [E4-R1] are accepted, while invalid/unknown IDs are rejected."""
    async def _run():
        mock_provider = AsyncMock()

        reply_item = ThreadReplyItem(
            reply_id="R1",
            author="김영희",
            timestamp="2026-08-27",
            text="2026.08.27 간담회 논의 내용 공유",
            permalink="https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787709893.004509",
            channel_id="C01",
            message_ts="1788000000.000001",
            thread_ts="1787709893.004509",
        )

        ev_item = EvidenceItem(
            evidence_id="E4",
            source="slack",
            author="홍길동",
            timestamp="2026-08-20",
            text="[학진님 구두 논의 기록]",
            channel="C01",
            message_identifier="https://app.slack.com/archives/C01/p1787709893004509",
            thread_identifier="1787709893.004509",
            thread_replies=[reply_item],
            thread_context_status="COMPLETE",
        )

        package = EvidencePackage(
            package_id="pkg_test",
            query="26년 하반기 간담회",
            created_at="2026-09-04T00:00:00Z",
            evidence_items=[ev_item],
            retrieval_coverage="STRONG",
            thread_context_status="COMPLETE",
            is_synthetic=True,
        )

        analyzer = InvestigationAnalyzer(provider=mock_provider)

        mock_provider.generate_structured.return_value = (
            AnalysisResult(
                summary="간담회 일정 논의 확인됨",
                confirmed_facts=[
                    ConfirmedFact(
                        statement="2026.08.27 간담회 논의 진행됨",
                        supporting_evidence_ids=["E4-R1"],
                    )
                ],
                disputed_points=[],
                contradictions=[],
                assessment="확정됨",
                uncertainty="없음",
                finding_type="POSITIVE_SUPPORTED",
            ),
            LLMUsageStats(provider="mock", model="test-model", prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )

        result, _ = await analyzer.analyze(
            question=UserQuestion(question="26년 하반기 간담회 일정"),
            package=package,
            strict_citation_check=True,
        )
        assert "E4-R1" in result.cited_evidence_ids

        # Subcitation error test
        mock_provider.generate_structured.return_value = (
            AnalysisResult(
                summary="환각 인용 테스트",
                confirmed_facts=[
                    ConfirmedFact(statement="환각 사실", supporting_evidence_ids=["E4-R999"])
                ],
                disputed_points=[],
                contradictions=[],
                assessment="",
                uncertainty="",
            ),
            LLMUsageStats(provider="mock", model="test-model", prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )

        with pytest.raises(EvidenceCitationError):
            await analyzer.analyze(
                question=UserQuestion(question="테스트"),
                package=package,
                strict_citation_check=True,
            )

    asyncio.run(_run())


def test_orchestrator_expansion_budget_at_most_one(tmp_path):
    """Verify MAX_THREAD_EXPANSIONS = 1 is strictly enforced in orchestrator."""
    async def _run():
        mock_provider = AsyncMock()

        mock_provider.generate_structured.side_effect = [
            (
                InvestigationPlan(
                    objective="간담회 일정 파악",
                    search_queries=[SearchQuery(query="간담회 일정", purpose="일정 확인")],
                    questions_to_resolve=["일정"],
                ),
                LLMUsageStats(provider="mock", model="test-model", prompt_tokens=50, completion_tokens=20, total_tokens=70),
            ),
            (
                AnalysisResult(
                    summary="일정 확인 완료",
                    confirmed_facts=[
                        ConfirmedFact(statement="간담회 확인", supporting_evidence_ids=["E1-R1"])
                    ],
                    disputed_points=[],
                    contradictions=[],
                    assessment="확인됨",
                    uncertainty="없음",
                    finding_type="POSITIVE_SUPPORTED",
                ),
                LLMUsageStats(provider="mock", model="test-model", prompt_tokens=100, completion_tokens=50, total_tokens=150),
            ),
        ]

        reply1 = ThreadReplyItem(
            reply_id="R1",
            author="작성자",
            text="댓글 내용",
            permalink="https://app.slack.com/archives/C1/p1788000000000001?thread_ts=1787000000.000001",
            channel_id="C1",
            message_ts="1788000000.000001",
            thread_ts="1787000000.000001",
        )

        item1 = EvidenceItem(
            evidence_id="ev_01",
            source="synthetic",
            text="간담회 일정 논의 첫번째 후보",
            message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
            thread_identifier="1787000000.000001",
            has_thread=True,
            thread_replies=[],
            thread_context_status="NOT_ATTEMPTED",
        )
        item2 = EvidenceItem(
            evidence_id="ev_02",
            source="synthetic",
            text="간담회 일정 논의 두번째 후보",
            message_identifier="https://app.slack.com/archives/C2/p1787000000000002",
            thread_identifier="1787000000.000002",
            has_thread=True,
        )

        search_backend = SyntheticSearchBackend(corpus=[item1, item2])
        search_backend.read_thread_context = AsyncMock(
            return_value=([reply1], ThreadContextStatus.COMPLETE)
        )

        orch = InvestigationOrchestrator(
            provider=mock_provider,
            search_backend=search_backend,
            config=OrchestratorConfig(base_output_dir=tmp_path),
        )

        result = await orch.run_investigation(UserQuestion(question="간담회 일정"))

        assert search_backend.read_thread_context.call_count == 1
        assert result.evidence_package.thread_context_status == "COMPLETE"
        assert len(result.evidence_package.evidence_items[0].thread_replies) == 1

    asyncio.run(_run())


def test_history_store_roundtrip_with_thread_replies(tmp_path):
    """Verify SQLite history store serializes and restores nested thread replies with backward compatibility."""
    db_file = tmp_path / "test_history.db"
    store = InvestigationHistoryStore(db_path=str(db_file))
    store.init_db()

    reply = ThreadReplyItem(
        reply_id="R1",
        author="작성자",
        text="댓글 내용",
        permalink="https://app.slack.com/archives/C1/p1788000000000001?thread_ts=1787000000.000001",
        channel_id="C1",
        message_ts="1788000000.000001",
        thread_ts="1787000000.000001",
    )

    evidence = [
        {
            "id": "E1",
            "source": "Slack",
            "author": "홍길동",
            "timestamp": "2026-08-20",
            "channel": "C1",
            "snippet": "루트 본문",
            "permalink": "https://app.slack.com/archives/C1/p1787000000000001",
            "has_valid_permalink": True,
            "thread_replies": [reply.model_dump()],
            "thread_context_status": "COMPLETE",
        }
    ]

    saved = store.save_investigation(
        investigation_id="inv-thread-01",
        question="간담회 일정",
        answer="확인됨",
        status="SUCCESS",
        evidence=evidence,
    )
    assert saved is True

    record = store.get_investigation("inv-thread-01")
    assert record is not None
    assert len(record["evidence"]) == 1
    loaded_ev = record["evidence"][0]
    assert len(loaded_ev["thread_replies"]) == 1
    assert loaded_ev["thread_replies"][0]["reply_id"] == "R1"
    assert loaded_ev["thread_context_status"] == "COMPLETE"


def test_typed_thread_read_result_immutable_tuple():
    """Verify ThreadReadResult holds immutable tuple and is frozen dataclass."""
    from dataclasses import FrozenInstanceError
    from pesu_agent.search.webview_thread_reader import ThreadReadResult, ThreadReadFailureReason

    reply = ThreadReplyItem(
        reply_id="R1",
        author="작성자",
        text="댓글 내용",
        permalink="https://app.slack.com/archives/C1/p1788000000000001?thread_ts=1787000000.000001",
        channel_id="C1",
        message_ts="1788000000.000001",
        thread_ts="1787000000.000001",
    )

    res = ThreadReadResult(
        replies=(reply,),
        status=ThreadContextStatus.PARTIAL,
        failure_reason=None,
    )

    assert isinstance(res.replies, tuple)
    assert len(res.replies) == 1
    assert res.replies[0] is reply

    # Verify frozen dataclass mutation is rejected
    with pytest.raises(FrozenInstanceError):
        res.status = ThreadContextStatus.COMPLETE  # type: ignore

    # Verify tuple cannot be modified in-place
    assert not hasattr(res.replies, "append")


def test_typed_failure_reasons_and_public_status_unchanged():
    """Verify exact ThreadReadFailureReason enum values and ThreadContextStatus public invariance."""
    from pesu_agent.search.webview_thread_reader import ThreadReadFailureReason

    expected_reasons = {
        "TARGET_ROW_NOT_IN_CURRENT_DOM",
        "ROW_IDENTITY_AMBIGUOUS",
        "REPLY_BAR_UNAVAILABLE",
        "CLICK_CORRELATED_ROOT_FAILED",
        "MESSAGE_REGION_UNAVAILABLE",
        "EXTRACTION_FAILED",
        "ELIGIBILITY_FAILED",
        "IDENTITY_PARSE_FAILED",
    }
    actual_reasons = {e.value for e in ThreadReadFailureReason}
    assert actual_reasons == expected_reasons

    expected_statuses = {"NOT_ATTEMPTED", "COMPLETE", "PARTIAL", "UNAVAILABLE"}
    actual_statuses = {e.value for e in ThreadContextStatus}
    assert actual_statuses == expected_statuses


def test_read_thread_context_compatibility_wrapper():
    """Verify read_thread_context returns mutable list and ThreadContextStatus."""
    mock_host = MagicMock()
    reader = WebviewThreadReader(host=mock_host)

    # Ineligible candidate should return empty list and NOT_ATTEMPTED
    item = EvidenceItem(
        evidence_id="ev_01",
        source="synthetic",
        text="단독 메시지",
        message_identifier="https://app.slack.com/archives/C1/p1787000000000001",
        has_thread=False,
    )

    replies, status = reader.read_thread_context(item)
    assert isinstance(replies, list)
    assert len(replies) == 0
    assert status == ThreadContextStatus.NOT_ATTEMPTED


# ---------------------------------------------------------------------------
# 16. Task 11Q4-P2A: Canonical Thread Root Capture Invariants (Requirements A-K)
# ---------------------------------------------------------------------------

def test_p2a_req_a_root_currently_discarded_core_red():
    """Requirement A: Thread root row is captured separately from replies rather than discarded."""
    extract_res = {
        "ok": True,
        "replies": [
            # Root row
            {
                "href": "https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
                "text": "루트 본문 전체 내용입니다.",
                "author": "루트작성자",
            },
            # Reply row 1
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
                "text": "첫번째 댓글",
                "author": "댓글작성자1",
            },
            # Reply row 2
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000002?thread_ts=1787000000.000001",
                "text": "두번째 댓글",
                "author": "댓글작성자2",
            },
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate(
        msg_id="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
        thr_id="1787000000.000001",
    )

    result = reader.read_thread_result(cand)
    assert getattr(result, "root", None) is not None, "Root was discarded by parser!"
    assert result.root.text == "루트 본문 전체 내용입니다."
    assert result.root.author == "루트작성자"
    assert result.root.message_ts == "1787000000.000001"
    assert result.root.channel_id == "C01"
    assert len(result.replies) == 2
    assert [r.text for r in result.replies] == ["첫번째 댓글", "두번째 댓글"]


def test_p2a_req_b_strict_root_identity():
    """Requirement B: Row with matching text/author but wrong message_ts is NOT accepted as root."""
    extract_res = {
        "ok": True,
        "replies": [
            # Wrong message_ts
            {
                "href": "https://app.slack.com/archives/C01/p1787999999999999?thread_ts=1787000000.000001",
                "text": "루트 본문 전체 내용입니다.",
                "author": "루트작성자",
            },
            # Reply 1
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
                "text": "첫번째 댓글",
            },
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate(
        msg_id="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
        thr_id="1787000000.000001",
    )

    result = reader.read_thread_result(cand)
    assert getattr(result, "root", None) is None, "Row with wrong message_ts must not be accepted as root!"


def test_p2a_req_c_zero_root_match_fails_closed():
    """Requirement C: Zero root match fails closed (root unavailable / None)."""
    extract_res = {
        "ok": True,
        "replies": [
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
                "text": "첫번째 댓글",
            },
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000002?thread_ts=1787000000.000001",
                "text": "두번째 댓글",
            },
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate(
        msg_id="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
        thr_id="1787000000.000001",
    )

    result = reader.read_thread_result(cand)
    assert getattr(result, "root", None) is None
    assert len(result.replies) == 2


def test_p2a_req_d_multiple_root_matches_fails_closed():
    """Requirement D: More than one row resolving to target root identity fails closed (root is None)."""
    extract_res = {
        "ok": True,
        "replies": [
            # Root duplicate 1
            {
                "href": "https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
                "text": "루트 복제본 1",
            },
            # Root duplicate 2
            {
                "href": "https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
                "text": "루트 복제본 2",
            },
            # Reply 1
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
                "text": "첫번째 댓글",
            },
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate(
        msg_id="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
        thr_id="1787000000.000001",
    )

    result = reader.read_thread_result(cand)
    assert getattr(result, "root", None) is None, "Multiple root matches must fail closed, not pick first!"
    assert len(result.replies) == 1


def test_p2a_req_e_root_body_preservation():
    """Requirement E: Multi-paragraph / rich-text root body survives extraction cleanly."""
    multi_paragraph = "첫 번째 문단\n두 번째 문단\n세 번째 문단"
    extract_res = {
        "ok": True,
        "replies": [
            {
                "href": "https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
                "text": multi_paragraph,
                "author": "루트작성자",
            },
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
                "text": "첫번째 댓글",
            },
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate(
        msg_id="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
        thr_id="1787000000.000001",
    )

    result = reader.read_thread_result(cand)
    assert getattr(result, "root", None) is not None
    assert result.root.text == multi_paragraph


def test_p2a_req_f_root_and_reply_separation():
    """Requirement F: Root does not appear in replies[], reply ordering and IDs remain unchanged."""
    extract_res = {
        "ok": True,
        "replies": [
            {
                "href": "https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
                "text": "루트 본문",
            },
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
                "text": "댓글 1",
            },
            {
                "href": "https://app.slack.com/archives/C01/p1788000000000002?thread_ts=1787000000.000001",
                "text": "댓글 2",
            },
        ],
    }
    host = create_mock_host(extract_result=extract_res)
    reader = WebviewThreadReader(host=host)
    cand = create_eligible_candidate(
        msg_id="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
        thr_id="1787000000.000001",
    )

    result = reader.read_thread_result(cand)
    assert getattr(result, "root", None) is not None
    assert result.root.text not in [r.text for r in result.replies]
    assert [r.reply_id for r in result.replies] == ["R1", "R2"]
    assert [r.text for r in result.replies] == ["댓글 1", "댓글 2"]


def test_p2a_req_g_h_i_retrieval_coordinator_canonical_identity_and_provenance():
    """Requirements G, H, I: Hydration enriches SAME EvidenceItem, preserves canonical identity & provenance, 0 duplicate."""
    from pesu_agent.investigation.retrieval_coordinator import (
        ProductionInvestigationRetrievalCoordinator,
    )
    from pesu_agent.investigation.selector import get_canonical_evidence_key

    async def _run():
        item = EvidenceItem(
            evidence_id="ev_01",
            source="synthetic",
            text="검색 스니펫 (짧은 본문)",
            author="스니펫작성자",
            message_identifier="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
            permalink="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
            thread_identifier="1787000000.000001",
            has_thread=True,
            thread_reply_count=1,
        )
        key_before = get_canonical_evidence_key(item)

        search_backend = SyntheticSearchBackend(corpus=[item])

        # Mock thread reader returning root + reply
        from pesu_agent.search.webview_thread_reader import ThreadRootItem
        root_item = ThreadRootItem(
            text="루트 본문 전체 내용 (Hydrated full text)",
            permalink="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
            channel_id="C01",
            message_ts="1787000000.000001",
            thread_ts="1787000000.000001",
            author="스니펫작성자",
        )
        reply_item = ThreadReplyItem(
            reply_id="R1",
            author="댓글작성자",
            text="댓글 내용",
            permalink="https://app.slack.com/archives/C01/p1788000000000001?thread_ts=1787000000.000001",
            channel_id="C01",
            message_ts="1788000000.000001",
            thread_ts="1787000000.000001",
        )
        mock_result = ThreadReadResult(
            replies=(reply_item,),
            status=ThreadContextStatus.PARTIAL,
            failure_reason=None,
            resident_match_count=1,
            root=root_item,
        )

        coord = ProductionInvestigationRetrievalCoordinator(search_backend=search_backend)
        plan = InvestigationPlan(
            objective="스니펫 확인",
            search_queries=[SearchQuery(query="검색 스니펫", purpose="스니펫 확인")],
            questions_to_resolve=["스니펫"],
        )

        res = await coord.retrieve_precomputed(
            plan=plan,
            thread_reader_fn=lambda c: mock_result,
        )

        # Requirement I: Exactly 1 evidence item for this canonical message (no duplicate)
        assert len(res.evidence_package.evidence_items) == 1
        hydrated_item = res.evidence_package.evidence_items[0]

        # Requirement G: Canonical identity before == after
        key_after = get_canonical_evidence_key(hydrated_item)
        assert key_before == key_after
        # Root body replaces search snippet
        assert hydrated_item.text == "루트 본문 전체 내용 (Hydrated full text)"
        assert "검색 스니펫" not in hydrated_item.text

        # Requirement H: Query provenance preserved
        assert key_before in res.candidate_query_provenance
        assert len(res.candidate_query_provenance[key_before]) > 0

    asyncio.run(_run())


def test_p2a_req_j_existing_thread_failure_path_preserves_snippet():
    """Requirement J: When thread reading fails, existing search snippet is retained."""
    from pesu_agent.investigation.retrieval_coordinator import (
        ProductionInvestigationRetrievalCoordinator,
    )

    async def _run():
        item = EvidenceItem(
            evidence_id="ev_01",
            source="synthetic",
            text="검색 스니펫 원본",
            message_identifier="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
            permalink="https://app.slack.com/archives/C01/p1787000000000001?thread_ts=1787000000.000001",
            thread_identifier="1787000000.000001",
            has_thread=True,
            thread_reply_count=1,
        )
        search_backend = SyntheticSearchBackend(corpus=[item])

        failed_result = ThreadReadResult(
            replies=(),
            status=ThreadContextStatus.UNAVAILABLE,
            failure_reason=ThreadReadFailureReason.REPLY_BAR_UNAVAILABLE,
            resident_match_count=1,
            root=None,
        )

        coord = ProductionInvestigationRetrievalCoordinator(search_backend=search_backend)
        plan = InvestigationPlan(
            objective="검색 스니펫 확인",
            search_queries=[SearchQuery(query="검색 스니펫 원본", purpose="스니펫 확인")],
            questions_to_resolve=["스니펫"],
        )

        res = await coord.retrieve_precomputed(
            plan=plan,
            thread_reader_fn=lambda c: failed_result,
        )

        assert len(res.evidence_package.evidence_items) == 1
        ev = res.evidence_package.evidence_items[0]
        assert ev.text == "검색 스니펫 원본"
        assert ev.thread_context_status == "UNAVAILABLE"

    asyncio.run(_run())


def test_p2a_req_k_safety_surface_regression():
    """Requirement K: Assert zero new UI actions, zero keyboard shortcuts, zero URL navigation."""
    all_js = (
        JS_CLEANUP_THREAD_OP_STATE
        + JS_INSPECT_RESIDENT_SEARCH_ROWS
        + JS_CLICK_RESIDENT_ROW_REPLY_BAR
        + JS_POLL_ROOT_DELTA
        + JS_EXTRACT_MOUNTED_THREAD_REPLIES
    )
    # 0 keyboard input
    assert ".blur()" not in all_js
    assert ".focus()" not in all_js
    assert "dispatchEvent" not in all_js
    assert "KeyboardEvent" not in all_js
    assert "InputEvent" not in all_js
    assert "keydown" not in all_js
    assert "keyup" not in all_js
    assert "keypress" not in all_js

    # 0 generic navigation
    assert THREAD_READER_URL_NAVIGATION_FALLBACK is False

    # 0 composer interaction
    assert COMPOSER_ROW_CAN_BECOME_THREAD_REPLY is False
