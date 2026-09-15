"""Deterministic Offline Tests for Slack Search Query Application & Result Transition Forensics (Phase 13.5D Task 11R2).

Validates:
1. Hard Requirement 1: Query transition and Result transition evaluated on separate axes.
2. Hard Requirement 2: Existing setter and submit semantics are unchanged and side-effect free.
3. Case A: Clean initial search transition from unmounted/empty to Result Set A.
4. Case B: Stale DOM race reproduction: Q2 entered while Result Set A lingers past extraction wait.
   Verifies current backend reads stale Result Set A and telemetry flags the unchanged fingerprint.
5. Case C: Transition never occurs: Q2 submitted but Slack surface remains un-transitioned.
6. Zero wall-clock dependency: uses deterministic mock host / patched sleep.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import patch
import pytest

from pesu_agent.search.webview_slack_search import (
    WebViewSlackSearchBackend,
    WebViewRuntimeState,
    compute_result_set_fingerprint,
    JS_SET_QUERY_AND_SUBMIT,
    WebViewSearchError,
    WebViewSearchFailureCode,
)


class DeterministicMockWebViewHost:
    """Deterministic mock WebView host simulating Slack search surface states."""

    def __init__(self) -> None:
        self.auth_ready: bool = True
        self.activated: bool = True
        self.input_ready: bool = True
        self.input_value: str = ""
        self.surface_query: str = ""
        self.current_results: list[dict[str, Any]] = []
        self.search_view_mounted_before: bool = False
        self.loading_indicator: bool = False
        self.submit_called: bool = False
        self.query_history: list[str] = []
        # Task 11R3 simulation controls:
        self.require_verified_state_for_effective_submit: bool = False
        self.effective_submit_triggered: bool = False
        self.pending_next_results: list[dict[str, Any]] = []
        self.simulate_slack_hang_no_transition: bool = False
        self.query_state_verified: bool = False

    def evaluate_js(self, script: str) -> Any:
        # 1. Auth & Readiness
        if "searchButtons.length === 1" in script or "isLoginScreen" in script:
            return {
                "ready": self.auth_ready,
                "auth_required": not self.auth_ready,
                "search_button_count": 1 if self.auth_ready else 0,
            }

        # 2. Activation
        if "top_nav_search" in script and "mousedown" in script:
            return {"activated": self.activated}

        # 3. Input Readiness
        if "searchModalCandidates" in script or "SEARCH_INPUT_NOT_MOUNTED" in script:
            return {
                "ready": self.input_ready,
                "modal_count": 1,
                "input_count": 1,
                "ambiguous": False,
            }

        # 4. Pre-Submit Probe
        if "JS_PROBE_PRE_SUBMIT_SEARCH_STATE" in script or "pre_submit_items" in script:
            mounted = self.search_view_mounted_before
            return {
                "input_value_before": self.input_value,
                "surface_query_before": self.surface_query or None,
                "search_view_mounted": mounted,
                "pre_submit_item_count": len(self.current_results) if mounted else 0,
                "pre_submit_items": self.current_results if mounted else [],
                "loading_detected_before": self.loading_indicator,
            }

        # 5. Synchronous Setter & Submit (Old JS_SET_QUERY_AND_SUBMIT)
        if "textyObj.setContents" in script and "searchInputBoxInstance.onEnter" in script:
            self.submit_called = True
            if self.require_verified_state_for_effective_submit:
                # Synchronous setContents + onEnter in the same tick executes against stale React state!
                # Result: onEnter is a NO-OP in Slack.
                self.effective_submit_triggered = False
            else:
                self.effective_submit_triggered = True
                if self.pending_next_results:
                    self.current_results = list(self.pending_next_results)
            return {"ok": True, "submitted": True}

        # 5b. Decoupled Texty Query Setter (JS_SET_TEXTY_QUERY)
        if "textyObj.setContents" in script and "searchInputBoxInstance.onEnter" not in script:
            return {"ok": True, "setter_succeeded": True}

        # 5c. Decoupled onEnter Invocation & Verification (JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER)
        if "searchInputBoxInstance.onEnter" in script and "textyObj.setContents" not in script:
            self.submit_called = True
            if self.simulate_slack_hang_no_transition:
                self.effective_submit_triggered = False
            else:
                self.effective_submit_triggered = True
                if self.pending_next_results:
                    self.current_results = list(self.pending_next_results)
            return {
                "ok": True,
                "report": {
                    "submit_succeeded": True,
                    "preconditions_satisfied": True,
                    "query_state_all_synced": True,
                    "strong_search_input_box_match_count": 1,
                    "duplicate_search_input_box_count": 0,
                    "onenter_callable": True,
                    "submit_attempt_count": 1,
                    "submit_method": "SearchInputBox.onEnter",
                },
                "submitted": True,
            }

        # 5d. Pure Query State Sync Verification
        if "query_state_all_synced" in script or "surfaces_synced" in script or "displayQueryOps" in script:
            self.query_state_verified = True
            return {
                "ok": True,
                "query_state_all_synced": True,
                "redux_status": "TRUE",
                "preconditions_satisfied": True,
                "surfaces_synced": {"dom": True, "quill_text": True, "quill_delta": True, "texty": True, "redux": True},
            }

        # 6. Post-Submit Probe
        if "JS_PROBE_POST_SUBMIT_INPUT_STATE" in script or "input_value_after_submit" in script:
            return {
                "input_value_after_submit": self.input_value,
                "surface_query_immediate": self.surface_query or None,
                "loading_detected_immediate": self.loading_indicator,
            }

        # 7. Post-Wait Probe
        if "JS_PROBE_POST_WAIT_SEARCH_STATE" in script or "surface_query_after" in script:
            sq = self.surface_query
            if self.require_verified_state_for_effective_submit and self.effective_submit_triggered and not self.simulate_slack_hang_no_transition:
                sq = self.input_value
            return {
                "surface_query_after": sq or None,
                "loading_detected_after": self.loading_indicator,
            }

        # 8. Result Extraction
        if "JS_EXTRACT_SEARCH_RESULTS" in script or "searchViews" in script:
            if not self.current_results:
                return {"ok": True, "terminal_state": "EMPTY_RESULTS", "items": []}
            return {
                "ok": True,
                "terminal_state": "RESULTS",
                "items": self.current_results,
            }

        return {"ok": True}


def _make_sample_item(idx: int, channel: str, ts: str, author: str, body: str) -> dict[str, Any]:
    plink = f"https://workspace.slack.com/archives/{channel}/p{ts.replace('.', '')}"
    return {
        "result_index": idx,
        "qualifying_permalinks": [plink],
        "sender_text": author,
        "sender_author_id": f"U{idx:08d}",
        "sender_valid": True,
        "timestamp_raw": "오후 2:00",
        "timestamp_valid": True,
        "thread_reply_detected": False,
        "primary_fragments": [body],
        "ambiguous_fragment_count": 0,
        "normalized_body": body,
        "body_valid": True,
    }


def test_deterministic_fingerprint_helper():
    """Verify compute_result_set_fingerprint is deterministic and sensitive to content."""
    items_a = [
        _make_sample_item(1, "C123", "1724738400.000001", "이주영", "본문 A"),
        _make_sample_item(2, "C456", "1724738400.000002", "김서경", "본문 B"),
    ]
    items_a_copy = [
        _make_sample_item(1, "C123", "1724738400.000001", "이주영", "본문 A"),
        _make_sample_item(2, "C456", "1724738400.000002", "김서경", "본문 B"),
    ]
    items_b = [
        _make_sample_item(1, "C123", "1724738400.000001", "이주영", "다른 본문"),
    ]

    fp_a1 = compute_result_set_fingerprint(items_a)
    fp_a2 = compute_result_set_fingerprint(items_a_copy)
    fp_b = compute_result_set_fingerprint(items_b)
    fp_empty = compute_result_set_fingerprint([])

    assert fp_a1 == fp_a2
    assert fp_a1 != fp_b
    assert fp_empty == "EMPTY_RESULTS"


def test_case_a_clean_initial_transition():
    """Case A: Clean initial search transition from unmounted DOM to Result Set A."""
    host = DeterministicMockWebViewHost()
    backend = WebViewSlackSearchBackend(host=host)

    q1 = "고객 만족도 현황 페이지 1차 배포 완료"
    host.input_value = q1
    host.surface_query = q1
    host.current_results = [
        _make_sample_item(1, "C04H83XRPA7", "1786601410.352619", "이주영", "지점 개설 미팅"),
        _make_sample_item(2, "C04TV5EUN2X", "1783409984.838889", "김서경", "기수임사에 대한 요율"),
    ]

    with patch("time.sleep", return_value=None):
        results = backend.search_messages(q1, max_results=5)

    assert len(results) == 2
    telemetry = backend.last_search_telemetry
    forensic = telemetry.get("forensic_transition", {})

    assert forensic["INPUT_MATCHES_REQUESTED"] is True
    assert forensic["SUBMIT_TRIGGERED"] is True
    assert forensic["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] is True
    assert forensic["RESULT_FINGERPRINT_BEFORE"] is None  # initially unmounted
    assert forensic["RESULT_FINGERPRINT_AFTER"] is not None
    assert forensic["TRANSITION_CLASSIFICATION"] == "RESULT_TRANSITIONED_AND_DIFFERENT_RESULTS"
    assert forensic["input_query_changed_but_result_fingerprint_unchanged"] is False


def test_case_b_stale_dom_race_reproduction_without_wall_clock():
    """Case B: Stale DOM race reproduction.

    Q2 is entered and submitted, but the DOM retains Result Set A.
    Verifies that the current backend extracts the stale Set A, and forensic telemetry
    accurately flags RESULT_SET_UNCHANGED_AFTER_QUERY_CHANGE.
    """
    host = DeterministicMockWebViewHost()
    backend = WebViewSlackSearchBackend(host=host)

    # Initial search Q1 produces Set A
    q1 = "고객 만족도 현황 페이지 1차 배포 완료"
    host.input_value = q1
    host.surface_query = q1
    set_a_items = [
        _make_sample_item(1, "C04H83XRPA7", "1786601410.352619", "이주영", "지점 개설 미팅"),
        _make_sample_item(2, "C04TV5EUN2X", "1783409984.838889", "김서경", "기수임사에 대한 요율"),
    ]
    host.current_results = set_a_items

    with patch("time.sleep", return_value=None):
        backend.search_messages(q1, max_results=5)
    fp_q1 = backend.last_search_telemetry["forensic_transition"]["RESULT_FINGERPRINT_AFTER"]

    # Subsequent search Q2: requested query changes, surface query changes to Q2,
    # but mock simulates Slack lag: current_results DOM is NOT yet replaced (stale Set A remains)
    q2 = "고객 만족도 현황 페이지 배포"
    host.input_value = q2
    host.surface_query = q2  # Surface URL updated, but results are stale!
    host.search_view_mounted_before = True  # search view is mounted from Q1!

    with patch("time.sleep", return_value=None):
        results_q2 = backend.search_messages(q2, max_results=5)

    telemetry_q2 = backend.last_search_telemetry
    forensic_q2 = telemetry_q2.get("forensic_transition", {})

    # The backend extracted the stale items
    assert len(results_q2) == 2
    # Verify pre-submit fingerprint matched Q1's fingerprint
    assert forensic_q2["RESULT_FINGERPRINT_BEFORE"] == fp_q1
    # Verify post-wait fingerprint also matched Q1's fingerprint (stale read)
    assert forensic_q2["RESULT_FINGERPRINT_AFTER"] == fp_q1
    assert forensic_q2["RESULT_FINGERPRINT_CHANGED"] is False

    # Hard Requirement 1 asserts:
    assert forensic_q2["INPUT_MATCHES_REQUESTED"] is True
    assert forensic_q2["SUBMIT_TRIGGERED"] is True
    assert forensic_q2["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] is True
    assert forensic_q2["input_query_changed_but_result_fingerprint_unchanged"] is True
    assert forensic_q2["TRANSITION_CLASSIFICATION"] == "RESULT_SET_UNCHANGED_AFTER_QUERY_CHANGE"


def test_case_c_transition_never_occurs():
    """Case C: Query submitted but Slack surface and DOM never transition.

    Verifies telemetry flags QUERY_SUBMITTED_BUT_SURFACE_NOT_TRANSITIONED.
    """
    host = DeterministicMockWebViewHost()
    backend = WebViewSlackSearchBackend(host=host)

    # Pre-existing state: surface has Q1, DOM has Set A
    q1 = "고객 만족도 현황 페이지 1차 배포 완료"
    host.input_value = q1
    host.surface_query = q1
    host.current_results = [
        _make_sample_item(1, "C04H83XRPA7", "1786601410.352619", "이주영", "지점 개설 미팅"),
    ]

    with patch("time.sleep", return_value=None):
        backend.search_messages(q1, max_results=5)

    # Q2 submitted, but host simulates Slack UI totally hanging:
    # surface_query remains Q1! (input matched, submit triggered, but surface never updated)
    q2 = "Office 영업지원 조회"
    host.input_value = q2
    host.surface_query = q1  # Slack failed to update search surface!
    host.search_view_mounted_before = True

    with patch("time.sleep", return_value=None):
        with pytest.raises(WebViewSearchError) as exc_info:
            backend.search_messages(q2, max_results=5)

    forensic = exc_info.value.details.get("forensic_transition", {})
    assert forensic["INPUT_MATCHES_REQUESTED"] is True
    assert forensic["SUBMIT_TRIGGERED"] is True
    assert forensic["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] is False
    assert forensic["TRANSITION_CLASSIFICATION"] == "QUERY_SUBMITTED_BUT_SURFACE_NOT_TRANSITIONED"


def test_setter_and_submit_side_effect_purity():
    """Hard Requirement 2: Decoupled model-native setter & verified submit pipeline structure.

    Verifies:
    - QUERY_SETTER_BEHAVIOR_CHANGED = False (Model-native Texty.setContents source="api")
    - SUBMIT_BEHAVIOR_CHANGED = True (Decoupled with verified state settle before onEnter)
    - Pipeline ordering: pre-probe -> set_texty -> pre-submit verify onEnter -> post-probe -> active barrier -> post-wait -> extract
    """
    src = inspect.getsource(WebViewSlackSearchBackend._execute_search_pipeline)

    idx_pre_probe = src.find("JS_PROBE_PRE_SUBMIT_SEARCH_STATE")
    idx_set_query = src.find("JS_SET_TEXTY_QUERY")
    idx_submit = src.find("JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER")
    idx_post_sub_probe = src.find("JS_PROBE_POST_SUBMIT_INPUT_STATE")
    idx_barrier = src.find("Bounded Active Transition Barrier")
    idx_post_wait_probe = src.find("JS_PROBE_POST_WAIT_SEARCH_STATE")
    idx_extract = src.find("JS_EXTRACT_SEARCH_RESULTS")

    assert idx_pre_probe != -1
    assert idx_set_query != -1
    assert idx_submit != -1
    assert idx_post_sub_probe != -1
    assert idx_barrier != -1
    assert idx_post_wait_probe != -1
    assert idx_extract != -1

    assert idx_pre_probe < idx_set_query < idx_submit < idx_post_sub_probe < idx_barrier < idx_post_wait_probe < idx_extract

    # Model-native setter purity
    from pesu_agent.search.webview_slack_search import JS_SET_TEXTY_QUERY, JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER
    assert "textyObj.setContents" in JS_SET_TEXTY_QUERY
    assert "source: 'api'" in JS_SET_TEXTY_QUERY
    assert "searchInputBoxInstance.onEnter()" in JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER


# =========================================================================
# Task 11R3 TDD Tests: Submission Defect & Transition Barrier
# =========================================================================

def test_task11r3_effective_submission_requires_state_settle_and_transitions_to_new_results():
    """Task 11R3 TDD RED Test 1: Submission requires verified query state settle.

    Real Slack behavior:
    - Q1 submitted -> Result Set A.
    - Q2 applied -> if onEnter is called synchronously before React state settles,
      it is a NO-OP and Result Set A stays in DOM.
    - When backend properly verifies query state settle before onEnter,
      effective submit triggers -> search surface and DOM transition to Result Set B.
    - Backend MUST return Result Set B for Q2 (never stale Result Set A).
    """
    host = DeterministicMockWebViewHost()
    host.require_verified_state_for_effective_submit = True
    backend = WebViewSlackSearchBackend(host=host)

    # Step 1: Initial query Q1 produces Result Set A
    q1 = "고객 만족도 현황 페이지 1차 배포 완료"
    host.input_value = q1
    host.surface_query = q1
    set_a_items = [
        _make_sample_item(1, "C04H83XRPA7", "1786601410.352619", "이주영", "지점 개설 미팅"),
        _make_sample_item(2, "C04TV5EUN2X", "1783409984.838889", "김서경", "기수임사에 대한 요율"),
    ]
    host.current_results = list(set_a_items)

    with patch("time.sleep", return_value=None):
        results_q1 = backend.search_messages(q1, max_results=5)

    assert len(results_q1) == 2
    assert results_q1[0].text == "지점 개설 미팅"

    # Step 2: Query Q2 applied
    q2 = "고객 만족도 현황 페이지 배포"
    host.input_value = q2
    host.search_view_mounted_before = True  # search view mounted from Q1
    set_b_items = [
        _make_sample_item(1, "C09DQ4Y7YNA", "1789088348.763469", "박정환", "배포 상태 최종 확인"),
    ]
    host.pending_next_results = list(set_b_items)

    with patch("time.sleep", return_value=None):
        results_q2 = backend.search_messages(q2, max_results=5)

    # Must return Result Set B, NOT stale Result Set A!
    assert len(results_q2) == 1
    assert results_q2[0].text == "배포 상태 최종 확인"
    assert results_q2[0].query == q2


def test_task11r3_stale_result_fail_closed_barrier():
    """Task 11R3 TDD RED Test 2: Stale results must never be returned as success.

    When requested query changed, previous result fingerprint still mounted,
    and no query-specific transition ever occurs:
    Backend MUST fail closed (raise WebViewSearchError) and NEVER return stale results.
    """
    host = DeterministicMockWebViewHost()
    backend = WebViewSlackSearchBackend(host=host)

    # Q1 succeeds with Set A
    q1 = "고객 만족도 현황 페이지 1차 배포 완료"
    host.input_value = q1
    host.surface_query = q1
    set_a_items = [
        _make_sample_item(1, "C04H83XRPA7", "1786601410.352619", "이주영", "지점 개설 미팅"),
    ]
    host.current_results = list(set_a_items)

    with patch("time.sleep", return_value=None):
        backend.search_messages(q1, max_results=5)

    # Q2 submitted, but Slack hangs indefinitely (no transition ever occurs)
    q2 = "완전히 다른 새 쿼리"
    host.input_value = q2
    host.surface_query = q1  # Surface stays on Q1!
    host.search_view_mounted_before = True
    host.simulate_slack_hang_no_transition = True

    # Backend MUST fail closed rather than returning stale Set A
    with patch("time.sleep", return_value=None):
        with pytest.raises(WebViewSearchError) as exc_info:
            backend.search_messages(q2, max_results=5)

    assert exc_info.value.failure_code in (
        WebViewSearchFailureCode.SEARCH_RESULT_TIMEOUT,
        WebViewSearchFailureCode.SEARCH_NAVIGATION_FAILED,
    )


def test_task11r4_t7_phrase_conjunction_quote_preservation_offline():
    """Task 11R4 Offline Verification: T7 quoted phrase syntax is preserved end-to-end.

    Verifies:
    - T7_REQUESTED_QUERY: '"고객 만족도" "1차 배포"'
    - T7_EFFECTIVE_INPUT_QUERY: '"고객 만족도" "1차 배포"'
    - T7_QUOTES_PRESERVED: True
    - T7_SYNTAX_SUPPORTED: True
    """
    import json
    from pesu_agent.search.slack_query_compiler import SlackQueryCompiler

    q_t7 = '"고객 만족도" "1차 배포"'
    compiled = SlackQueryCompiler.compile(q_t7)

    assert compiled.compiled_query == q_t7
    assert compiled.outer_quotes_removed is False
    assert compiled.complexity.quoted_phrase_count == 2

    host = DeterministicMockWebViewHost()
    backend = WebViewSlackSearchBackend(host=host)

    captured_js_payloads = []
    orig_eval = host.evaluate_js

    def spy_eval(script):
        captured_js_payloads.append(script)
        return orig_eval(script)

    host.evaluate_js = spy_eval

    with patch("time.sleep", return_value=None):
        backend.search_messages(q_t7, max_results=5)

    # 1. Verify JS_SET_TEXTY_QUERY was invoked with exact quoted string
    set_query_calls = [s for s in captured_js_payloads if "JS_SET_TEXTY_QUERY" in s or "targetQuery" in s]
    assert len(set_query_calls) >= 1
    # json.dumps(q_t7) produces '"\"고객 만족도\" \"1차 배포\""'
    expected_arg = json.dumps(q_t7)
    assert f"({expected_arg})" in set_query_calls[0]

    # 2. Verify JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER was invoked with exact quoted string
    submit_calls = [s for s in captured_js_payloads if "searchInputBoxInstance.onEnter()" in s]
    assert len(submit_calls) >= 1
    assert f"({expected_arg})" in submit_calls[0]


    # 3. Telemetry records requested query
    telemetry = backend.last_search_telemetry
    forensic = telemetry.get("forensic_transition", {})
    assert forensic.get("requested_query") == q_t7




