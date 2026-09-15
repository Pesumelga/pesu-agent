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

        # 5. Setter & Submit (JS_SET_QUERY_AND_SUBMIT)
        if "textyObj.setContents" in script and "searchInputBoxInstance.onEnter" in script:
            self.submit_called = True
            # In Slack, input text updates to targetQuery
            # Extract targetQuery if present
            return {"ok": True, "submitted": True}

        # 6. Post-Submit Probe
        if "JS_PROBE_POST_SUBMIT_INPUT_STATE" in script or "input_value_after_submit" in script:
            return {
                "input_value_after_submit": self.input_value,
                "surface_query_immediate": self.surface_query or None,
                "loading_detected_immediate": self.loading_indicator,
            }

        # 7. Post-Wait Probe
        if "JS_PROBE_POST_WAIT_SEARCH_STATE" in script or "surface_query_after" in script:
            return {
                "surface_query_after": self.surface_query or None,
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
        backend.search_messages(q2, max_results=5)

    forensic = backend.last_search_telemetry.get("forensic_transition", {})
    assert forensic["INPUT_MATCHES_REQUESTED"] is True
    assert forensic["SUBMIT_TRIGGERED"] is True
    assert forensic["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] is False
    assert forensic["TRANSITION_CLASSIFICATION"] == "QUERY_SUBMITTED_BUT_SURFACE_NOT_TRANSITIONED"


def test_setter_and_submit_side_effect_purity():
    """Hard Requirement 2: Existing setter / submit semantics unchanged.

    Verifies:
    - QUERY_SETTER_BEHAVIOR_CHANGED = False
    - SUBMIT_BEHAVIOR_CHANGED = False
    - FORENSIC_TELEMETRY_SIDE_EFFECT_FREE = True
    """
    src = inspect.getsource(WebViewSlackSearchBackend._execute_search_pipeline)

    # 1. Pipeline structure must be: pre-submit probe -> JS_SET_QUERY_AND_SUBMIT -> post-submit probe -> wait -> post-wait probe
    idx_pre_probe = src.find("JS_PROBE_PRE_SUBMIT_SEARCH_STATE")
    idx_set_and_submit = src.find("JS_SET_QUERY_AND_SUBMIT")
    idx_post_sub_probe = src.find("JS_PROBE_POST_SUBMIT_INPUT_STATE")
    idx_wait = src.find("time.sleep(3.5)")
    idx_post_wait_probe = src.find("JS_PROBE_POST_WAIT_SEARCH_STATE")
    idx_extract = src.find("JS_EXTRACT_SEARCH_RESULTS")

    assert idx_pre_probe != -1
    assert idx_set_and_submit != -1
    assert idx_post_sub_probe != -1
    assert idx_wait != -1
    assert idx_post_wait_probe != -1
    assert idx_extract != -1

    assert idx_pre_probe < idx_set_and_submit < idx_post_sub_probe < idx_wait < idx_post_wait_probe < idx_extract

    # 2. JS_SET_QUERY_AND_SUBMIT must remain frozen model-native setter
    assert "textyObj.setContents({ contents: [{ insert: targetQuery }] }, { source: 'api' })" in JS_SET_QUERY_AND_SUBMIT
    assert "searchInputBoxInstance.onEnter()" in JS_SET_QUERY_AND_SUBMIT
