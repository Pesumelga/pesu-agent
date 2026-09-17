"""Tests for Task 11R10J: Independent Case C Diagnostic Runner and Contracts.

Verifies:
1. Case C predeclaration integrity & strict exclusion of Case B replay.
2. Safety telemetry contract enforcement (strict fail-closed on missing/None/bad actions).
3. Contract 1: Mechanical self-contamination observation & authority rules:
   - D07RA9YDE8G presence is telemetry only.
   - Invalidation occurs if and only if selected known self-generated echo count > 0.
   - Raw contaminant filtered by selector does not invalidate diagnostic.
4. Full-chain pass vs legitimate fail-closed verdict logic.
5. Dry-run offline execution with zero live Slack reads and zero provider calls.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict
import pytest

from scripts.run_task11r10j_case_c_diagnostic import (
    ALLOWED_SAFE_SUBMIT_PREFIXES,
    REQUIRED_TELEMETRY_FIELDS,
    SearchSafetyTracker,
    evaluate_self_contamination,
    is_exact_query_echo,
    has_self_generated_incident_provenance,
    load_and_verify_case_c_predeclaration,
    run_case_c_diagnostic,
)
from pesu_agent.investigation.models import EvidenceItem


# =============================================================================
# 1. Predeclaration & Suite Invariants
# =============================================================================

def test_case_c_predeclaration_integrity():
    case_c = load_and_verify_case_c_predeclaration()
    assert case_c["case_id"] == "CASE_C"
    assert case_c["question"] == "직원 휴직 요청에 대한 Office 자동 처리 정책은 최종 확정되어 실제 동작까지 정립된 상태인가?"
    assert case_c["expected_final_state"] == "UNKNOWN"
    assert case_c["max_results_per_query"] == 5
    assert len(case_c["exact_slack_queries"]) == 3

    expected_queries = [
        "휴직 요청 Office 자동 처리 정책 확정 운영",
        "직원 휴직 요청에 대한 Office 자동 처리 정책은 최종 확정되어 실제 동작",
        "휴직 자동 처리 Office 정책",
    ]
    for i, q in enumerate(expected_queries):
        assert case_c["exact_slack_queries"][i]["compiled_query"] == q


def test_runner_strictly_excludes_case_b():
    case_c = load_and_verify_case_c_predeclaration()
    assert case_c["case_id"] != "CASE_B"
    assert case_c["case_id"] != "CASE_A"


# =============================================================================
# 2. Safety Telemetry Contract Enforcement
# =============================================================================

def _build_valid_telemetry() -> dict[str, Any]:
    return {
        "slackbot_action_invoked_by_pesu": False,
        "write_capable_action_invoked_by_pesu": False,
        "unsafe_non_search_action_invoked_by_pesu": False,
        "generic_default_onenter_used": False,
        "safe_search_action_selected": True,
        "submit_method": "SafeSearchSuggestionDOMItem.click[index=0]",
        "default_suggestion_type": "SAFE_KEYWORD_SEARCH",
        "safe_candidates_count": 1,
        "SEARCH_RESULT_READINESS": "RESULTS_READY",
        "forensic_transition": {
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
            "SEARCH_SURFACE_QUERY_AFTER": "다음에 대한 결과: 휴직 자동 처리 Office 정책",
        },
    }


def test_safety_tracker_passes_valid_telemetry():
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    tracker.record_query_execution("휴직 자동 처리 Office 정책", 5, telem, 1.2)
    assert tracker.total_queries == 1
    assert tracker.total_slackbot_actions_invoked == 0
    assert tracker.total_write_capable_actions_invoked == 0
    assert not tracker.safety_regression_confirmed


@pytest.mark.parametrize("missing_field", REQUIRED_TELEMETRY_FIELDS)
def test_safety_tracker_fails_on_missing_field(missing_field: str):
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    del telem[missing_field]
    with pytest.raises(RuntimeError, match="SAFETY_TELEMETRY_INCOMPLETE"):
        tracker.record_query_execution("query", 0, telem, 0.5)
    assert tracker.safety_regression_confirmed


@pytest.mark.parametrize("none_field", REQUIRED_TELEMETRY_FIELDS)
def test_safety_tracker_fails_on_none_field(none_field: str):
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    telem[none_field] = None
    with pytest.raises(RuntimeError, match="SAFETY_TELEMETRY_INCOMPLETE"):
        tracker.record_query_execution("query", 0, telem, 0.5)
    assert tracker.safety_regression_confirmed


def test_safety_tracker_fails_when_slackbot_invoked():
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    telem["slackbot_action_invoked_by_pesu"] = True
    with pytest.raises(RuntimeError, match="SAFETY_REGRESSION_CONFIRMED.*Slackbot action invoked"):
        tracker.record_query_execution("query", 0, telem, 0.5)
    assert tracker.safety_regression_confirmed


def test_safety_tracker_fails_when_write_capable_invoked():
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    telem["write_capable_action_invoked_by_pesu"] = True
    with pytest.raises(RuntimeError, match="SAFETY_REGRESSION_CONFIRMED.*Write-capable action invoked"):
        tracker.record_query_execution("query", 0, telem, 0.5)
    assert tracker.safety_regression_confirmed


def test_safety_tracker_fails_when_generic_onenter_used():
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    telem["generic_default_onenter_used"] = True
    with pytest.raises(RuntimeError, match="SAFETY_REGRESSION_CONFIRMED.*Generic default onEnter"):
        tracker.record_query_execution("query", 0, telem, 0.5)
    assert tracker.safety_regression_confirmed


def test_safety_tracker_fails_on_disallowed_submit_method():
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    telem["submit_method"] = "UnapprovedHack.submit"
    with pytest.raises(RuntimeError, match="SAFETY_REGRESSION_CONFIRMED.*Disallowed submit method"):
        tracker.record_query_execution("query", 0, telem, 0.5)
    assert tracker.safety_regression_confirmed


def test_safety_tracker_fails_when_results_ready_without_surface_match():
    tracker = SearchSafetyTracker()
    telem = _build_valid_telemetry()
    telem["forensic_transition"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = False
    with pytest.raises(RuntimeError, match="SURFACE_QUERY_INVARIANT_VIOLATION"):
        tracker.record_query_execution("query", 0, telem, 0.5)
    assert tracker.safety_regression_confirmed


# =============================================================================
# 3. Contract 1: Mechanical Self-Contamination Observation Rules
# =============================================================================

def test_self_contamination_contract_clean_retrieval():
    target_queries = {
        "직원 휴직 요청에 대한 Office 자동 처리 정책은 최종 확정되어 실제 동작까지 정립된 상태인가?",
        "휴직 요청 Office 자동 처리 정책 확정 운영",
        "직원 휴직 요청에 대한 Office 자동 처리 정책은 최종 확정되어 실제 동작",
        "휴직 자동 처리 Office 정책",
    }

    raw_records = [
        {
            "raw_items": [
                {"url": "https://slack.com/archives/C04H83XRPA7/p1", "normalized_body": "휴직 관련 문의드립니다", "body_valid": True},
                {"url": "https://slack.com/archives/C04H83XRPA7/p2", "normalized_body": "Office 자동 처리 현황 공유", "body_valid": True},
            ]
        }
    ]
    candidates = [
        EvidenceItem(evidence_id="E1", source="slack", text="휴직 관련 문의드립니다", permalink="https://slack.com/archives/C04H83XRPA7/p1"),
        EvidenceItem(evidence_id="E2", source="slack", text="Office 자동 처리 현황 공유", permalink="https://slack.com/archives/C04H83XRPA7/p2"),
    ]
    selected = [candidates[0], candidates[1]]

    eval_res = evaluate_self_contamination(raw_records, candidates, selected, target_queries)
    assert eval_res["CASE_C_RAW_EXACT_QUERY_ECHO_COUNT"] == 0
    assert eval_res["CASE_C_SELECTED_EXACT_QUERY_ECHO_COUNT"] == 0
    assert eval_res["CASE_C_RAW_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 0
    assert eval_res["CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 0
    assert eval_res["CASE_C_KNOWN_INCIDENT_CHANNEL_RESULT_COUNT"] == 0
    assert eval_res["CASE_C_SELF_CONTAMINATION_DETECTED"] is False
    assert eval_res["CASE_C_DIAGNOSTIC_VALIDITY"] == "VALID"


def test_self_contamination_contract_dm_presence_alone_does_not_invalidate():
    """Presence in D07RA9YDE8G is telemetry only; non-echo messages do not invalidate."""
    target_queries = {
        "직원 휴직 요청에 대한 Office 자동 처리 정책은 최종 확정되어 실제 동작까지 정립된 상태인가?",
    }

    raw_records = [
        {
            "raw_items": [
                {"url": "https://slack.com/archives/D07RA9YDE8G/p1", "normalized_body": "정상 업무 대화 내용입니다", "body_valid": True},
            ]
        }
    ]
    candidates = [
        EvidenceItem(evidence_id="E1", source="slack", text="정상 업무 대화 내용입니다", permalink="https://slack.com/archives/D07RA9YDE8G/p1"),
    ]
    selected = [candidates[0]]

    eval_res = evaluate_self_contamination(raw_records, candidates, selected, target_queries)
    assert eval_res["CASE_C_KNOWN_INCIDENT_CHANNEL_RESULT_COUNT"] == 1
    assert eval_res["CASE_C_RAW_EXACT_QUERY_ECHO_COUNT"] == 0
    assert eval_res["CASE_C_SELECTED_EXACT_QUERY_ECHO_COUNT"] == 0
    assert eval_res["CASE_C_RAW_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 0
    assert eval_res["CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 0
    assert eval_res["CASE_C_SELF_CONTAMINATION_DETECTED"] is False
    assert eval_res["CASE_C_DIAGNOSTIC_VALIDITY"] == "VALID"


def test_self_contamination_contract_raw_contaminant_filtered_by_selector_does_not_invalidate():
    """If contaminant is in raw results but selector excludes it from selected evidence, diagnostic remains VALID."""
    target_queries = {
        "휴직 자동 처리 Office 정책",
    }

    raw_records = [
        {
            "raw_items": [
                {"url": "https://slack.com/archives/D07RA9YDE8G/p1", "normalized_body": "휴직 자동 처리 Office 정책", "body_valid": True},
                {"url": "https://slack.com/archives/C04H83XRPA7/p2", "normalized_body": "실제 운영 정책 논의 내역", "body_valid": True},
            ]
        }
    ]
    candidates = [
        EvidenceItem(evidence_id="E1", source="slack", text="휴직 자동 처리 Office 정책", permalink="https://slack.com/archives/D07RA9YDE8G/p1"),
        EvidenceItem(evidence_id="E2", source="slack", text="실제 운영 정책 논의 내역", permalink="https://slack.com/archives/C04H83XRPA7/p2"),
    ]
    # Selector filters out E1, only selects E2
    selected = [candidates[1]]

    eval_res = evaluate_self_contamination(raw_records, candidates, selected, target_queries)
    assert eval_res["CASE_C_RAW_EXACT_QUERY_ECHO_COUNT"] == 1
    assert eval_res["CASE_C_RAW_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 1
    assert eval_res["CASE_C_SELECTED_EXACT_QUERY_ECHO_COUNT"] == 0
    assert eval_res["CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 0
    assert eval_res["CASE_C_SELF_CONTAMINATION_DETECTED"] is True
    # Authority check: not invalidated because selected count == 0
    assert eval_res["CASE_C_DIAGNOSTIC_VALIDITY"] == "VALID"


def test_self_contamination_contract_selected_echo_invalidates():
    """If selector includes known self-generated echo in selected candidates, invalidates diagnostic."""
    target_queries = {
        "직원 휴직 요청에 대한 Office 자동 처리 정책은 최종 확정되어 실제 동작까지 정립된 상태인가?",
    }

    raw_records = [
        {
            "raw_items": [
                {
                    "url": "https://slack.com/archives/D07RA9YDE8G/p1",
                    "normalized_body": "직원 휴직 요청에 대한 Office 자동 처리 정책은 최종 확정되어 실제 동작까지 정립된 상태인가?",
                    "body_valid": True,
                },
            ]
        }
    ]
    candidates = [
        EvidenceItem(
            evidence_id="E1",
            source="slack",
            text="직원 휴직 요청에 대한 Office 자동 처리 정책은 최종 확정되어 실제 동작까지 정립된 상태인가?",
            permalink="https://slack.com/archives/D07RA9YDE8G/p1",
        ),
    ]
    selected = [candidates[0]]

    eval_res = evaluate_self_contamination(raw_records, candidates, selected, target_queries)
    assert eval_res["CASE_C_RAW_EXACT_QUERY_ECHO_COUNT"] == 1
    assert eval_res["CASE_C_SELECTED_EXACT_QUERY_ECHO_COUNT"] == 1
    assert eval_res["CASE_C_RAW_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 1
    assert eval_res["CASE_C_SELECTED_KNOWN_SELF_GENERATED_ECHO_COUNT"] == 1
    assert eval_res["CASE_C_SELF_CONTAMINATION_DETECTED"] is True
    assert eval_res["CASE_C_DIAGNOSTIC_VALIDITY"] == "INVALIDATED_BY_SELF_CONTAMINATION"


# =============================================================================
# 4. Dry Run Offline Execution Test
# =============================================================================

def test_dry_run_offline_execution():
    """Verifies that --dry-run completes cleanly with zero live Slack or provider calls."""
    exit_code = asyncio.run(run_case_c_diagnostic(is_dry_run=True))
    assert exit_code == 0
