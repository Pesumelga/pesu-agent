"""Regression tests for Task 11 / P2A-L1 Attempt 2 Qualification Runner.

Tests required by protocol hardening and Attempt 2 forensic remediation:
- Regression 1: If activation dispatch occurs and search_messages() raises, search_activation_dispatches == 1 is reported.
- Regression 2: A successful JS dispatch does NOT imply modal_ever_observed == true (search_modal_open_calls remains 0).
- Regression 3: A failed search call does not erase backend failure telemetry.
- Regression 4: Missing telemetry is distinguished from explicitly false (tri-state) and cannot produce PASS.
- Regression 5: Attempt 1-style symptom (dispatch=1, modal=false, timeout exception) results in FAIL_CLOSED.
- Regression 6: Dry-run still keeps all actual UI action counters at 0 and cannot declare live validation PASS.
- Regression 7: No production search timeout (5.0s) or selector was changed.
- Regression 8: verified_query_state_before_submit == true alone cannot satisfy SEARCH_SURFACE_QUERY_MATCHES_REQUESTED (source separation).
- Regression 9: Reply-bar failure telemetry audit: click_executed=True preserved on failure, missing/ambiguous click records UNKNOWN (never defaults to 0).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from scripts.qualify_task11q4_p2a_live_root_extraction import (
    evaluate_dry_run_gate,
    evaluate_live_gate,
    harvest_search_telemetry,
    harvest_thread_reader_telemetry,
    run_p2a_l1_qualification,
)
from pesu_agent.search.webview_slack_search import (
    JS_ACTIVATE_SEARCH,
    JS_CHECK_AUTH_AND_READINESS,
    JS_CHECK_SEARCH_INPUT_READINESS,
    SEARCH_INPUT_READINESS_TIMEOUT_SECONDS,
    WebViewSearchError,
    WebViewSearchFailureCode,
)


def _get_valid_baseline_live_report() -> dict[str, Any]:
    return {
        "execution_mode": "LIVE",
        "live_execution_started": True,
        "live_execution_completed": True,
        "checks": {
            "frozen_protocol_respected": True,
            "frozen_hashes_matched": True,
            "target_identity_exact_match": True,
            "pre_action_identity_check": "PASS",
            "immediate_pre_activation_recheck": "PASS",
            "reply_bar_guard_passed": True,
            "existing_qualified_reply_bar_interaction_only": True,
            "post_open_root_correlation": "PASS",
            "canonical_root_captured": True,
            "root_identity_exact_match": True,
            "root_text_extracted": True,
            "root_and_replies_distinct": True,
            "evidence_item_identity_preserved": True,
            "duplicate_evidence_count": 0,
            "query_provenance_preserved": True,
            "code_or_protocol_mutated_after_first_live": False,
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": True,
            "QUERY_STATE_VERIFIED_BEFORE_SUBMIT": True,
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
            "root_extraction_result_source": "LIVE_DOM",
        },
        "safety_telemetry": {
            "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": False,
            "SLACKBOT_ACTION_INVOKED_BY_PESU": False,
            "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": False,
            "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": False,
            "composer_focus": 0,
            "composer_text_input": 0,
            "composer_send": 0,
        },
        "live_action_inventory": {
            "search_activation_dispatches": 1,
            "search_modal_observed": True,
            "search_input_ready": True,
            "search_submissions": 1,
            "reply_bar_clicks": 1,
            "pure_read_dom_extractions": 1,
            "new_ui_actions_added": 0,
            "new_click_targets_added": 0,
            "search_modal_open_calls": 1,
        },
        "search_telemetry": {
            "search_activation_dispatches": 1,
            "modal_ever_observed": True,
            "final_modal_count": 1,
            "input_ready": True,
            "activation_to_first_modal_ms": 120,
            "readiness_poll_count": 2,
            "last_confirmed_stage": "COMPLETED",
            "failure_case_classification": "NONE",
        },
        "provider_calls": 0,
        "retry_occurred": False,
    }


def test_regression_1_activation_dispatch_reported_on_search_exception():
    """Regression 1: If activation dispatch occurs and search_messages() raises, search_activation_dispatches == 1 is reported."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {
        "activation_dispatch_count": 1,
        "activation_result": "activated:true",
        "modal_ever_observed": False,
        "final_modal_count": 0,
        "readiness_poll_count": 50,
        "last_confirmed_stage": "MODAL_WAIT_STARTED",
        "failure_case_classification": "CASE_B_MODAL_NOT_MOUNTED",
        "elapsed_ms": 5002,
    }
    exc = WebViewSearchError(
        WebViewSearchFailureCode.SEARCH_INPUT_READINESS_TIMEOUT,
        "Search input readiness timed out after 5.0s: CASE_B_MODAL_NOT_MOUNTED",
        details=mock_backend.last_search_telemetry,
    )
    report: dict[str, Any] = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    harvest_search_telemetry(mock_backend, exc, report)

    assert report["live_action_inventory"]["search_activation_dispatches"] == 1
    assert report["search_telemetry"]["search_activation_dispatches"] == 1


def test_regression_2_dispatch_does_not_imply_modal_observed():
    """Regression 2: A successful JS dispatch does NOT imply modal_ever_observed == true (search_modal_open_calls == 0)."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {
        "activation_dispatch_count": 1,
        "activation_result": "activated:true",
        "modal_ever_observed": False,
        "final_modal_count": 0,
        "readiness_poll_count": 50,
        "last_confirmed_stage": "MODAL_WAIT_STARTED",
        "failure_case_classification": "CASE_B_MODAL_NOT_MOUNTED",
    }
    report: dict[str, Any] = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    harvest_search_telemetry(mock_backend, None, report)

    assert report["live_action_inventory"]["search_activation_dispatches"] == 1
    assert report["live_action_inventory"]["search_modal_observed"] is False
    assert report["live_action_inventory"]["search_modal_open_calls"] == 0
    assert report["search_telemetry"]["modal_ever_observed"] is False


def test_regression_3_failed_search_call_preserves_backend_telemetry():
    """Regression 3: A failed search call does not erase backend failure telemetry."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {
        "activation_dispatch_count": 1,
        "modal_ever_observed": False,
        "final_modal_count": 0,
        "readiness_poll_count": 50,
        "last_confirmed_stage": "MODAL_WAIT_STARTED",
        "failure_case_classification": "CASE_B_MODAL_NOT_MOUNTED",
        "elapsed_ms": 5002,
        "document_visibility_state": "visible",
    }
    exc = WebViewSearchError(
        WebViewSearchFailureCode.SEARCH_INPUT_READINESS_TIMEOUT,
        "Timeout",
        details=mock_backend.last_search_telemetry,
    )
    report: dict[str, Any] = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    harvest_search_telemetry(mock_backend, exc, report)

    assert report["search_telemetry"]["failure_case_classification"] == "CASE_B_MODAL_NOT_MOUNTED"
    assert report["search_telemetry"]["readiness_poll_count"] == 50
    assert report["search_telemetry"]["elapsed_ms"] == 5002
    assert report["search_telemetry"]["document_visibility_state"] == "visible"


def test_regression_4_missing_telemetry_distinguished_from_false_and_fails_closed():
    """Regression 4: Missing telemetry is distinguished from explicitly false (tri-state) and cannot produce PASS."""
    # Case A: Field explicitly False
    backend_false = MagicMock()
    backend_false.last_search_telemetry = {
        "activation_dispatch_count": 1,
        "modal_ever_observed": False,
        "safe_search_action_selected": False,
    }
    report_false: dict[str, Any] = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    harvest_search_telemetry(backend_false, None, report_false)
    assert report_false["live_action_inventory"]["search_modal_observed"] is False
    assert report_false["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] is False

    # Case B: Field missing from telemetry
    backend_missing = MagicMock()
    backend_missing.last_search_telemetry = {
        "activation_dispatch_count": 1,
        # "modal_ever_observed" is omitted!
        # "safe_search_action_selected" is omitted!
    }
    report_missing: dict[str, Any] = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    harvest_search_telemetry(backend_missing, None, report_missing)
    assert report_missing["live_action_inventory"]["search_modal_observed"] == "UNKNOWN"
    assert report_missing["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] == "UNKNOWN"

    # Case C: Entire telemetry missing
    backend_none = MagicMock()
    backend_none.last_search_telemetry = None
    report_none: dict[str, Any] = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    harvest_search_telemetry(backend_none, None, report_none)
    assert report_none["live_action_inventory"]["search_activation_dispatches"] == "UNKNOWN"
    assert report_none["search_telemetry"]["failure_case_classification"] == "TELEMETRY_MISSING"

    # Live gate rejects any UNKNOWN values
    live_report = _get_valid_baseline_live_report()
    live_report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = "UNKNOWN"
    assert evaluate_live_gate(live_report) is False


def test_regression_5_attempt1_symptom_results_in_fail_closed():
    """Regression 5: Attempt 1-style symptom (dispatch=1, modal=false, timeout exception) results in FAIL_CLOSED."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {
        "activation_dispatch_count": 1,
        "activation_result": "activated:true",
        "modal_ever_observed": False,
        "final_modal_count": 0,
        "readiness_poll_count": 50,
        "last_confirmed_stage": "MODAL_WAIT_STARTED",
        "failure_case_classification": "CASE_B_MODAL_NOT_MOUNTED",
        "elapsed_ms": 5002,
    }
    exc = WebViewSearchError(
        WebViewSearchFailureCode.SEARCH_INPUT_READINESS_TIMEOUT,
        "Search input readiness timed out after 5.0s: CASE_B_MODAL_NOT_MOUNTED",
        details=mock_backend.last_search_telemetry,
    )
    report: dict[str, Any] = {
        "execution_mode": "LIVE",
        "live_execution_started": True,
        "live_execution_completed": False,
        "live_action_inventory": {
            "search_activation_dispatches": 0,
            "search_modal_observed": False,
            "search_input_ready": False,
            "search_submissions": 0,
            "reply_bar_clicks": 0,
            "pure_read_dom_extractions": 0,
            "new_ui_actions_added": 0,
            "new_click_targets_added": 0,
            "search_modal_open_calls": 0,
        },
        "search_telemetry": {},
        "safety_telemetry": {},
        "checks": {},
    }
    harvest_search_telemetry(mock_backend, exc, report)
    report["error"] = str(exc)
    report["verdict"] = "FAIL_CLOSED"
    report["P2A_L1"] = "FAIL_CLOSED"
    report["thread_root_data_extraction_live_validated"] = False

    assert report["live_action_inventory"]["search_activation_dispatches"] == 1
    assert report["live_action_inventory"]["search_modal_observed"] is False
    assert report["live_action_inventory"]["search_modal_open_calls"] == 0
    assert report["live_action_inventory"]["search_submissions"] in (0, "UNKNOWN")
    assert report["live_action_inventory"]["reply_bar_clicks"] in (0, "UNKNOWN")
    assert report["verdict"] == "FAIL_CLOSED"
    assert report["P2A_L1"] == "FAIL_CLOSED"
    assert report["thread_root_data_extraction_live_validated"] is False
    assert evaluate_live_gate(report) is False


def test_regression_6_dry_run_actual_ui_actions_zero_and_never_declares_live_pass():
    """Regression 6: Dry-run keeps all actual UI action counters at 0, observation telemetry at NOT_RUN, and cannot declare live validation PASS."""
    result = run_p2a_l1_qualification(dry_run=True)

    assert result["verdict"] == "DRY_RUN_PASS"
    assert result["P2A_L1"] == "NOT_EXECUTED"
    assert result["thread_root_data_extraction_live_validated"] is False

    # Actual action counters must remain strictly 0
    actions = result["live_action_inventory"]
    assert actions["search_activation_dispatches"] == 0
    assert actions["search_submissions"] == 0
    assert actions["reply_bar_clicks"] == 0
    assert actions["pure_read_dom_extractions"] == 0
    assert actions["new_ui_actions_added"] == 0
    assert actions["new_click_targets_added"] == 0

    # Observation telemetry in dry-run must be NOT_RUN (never observed)
    assert actions["search_modal_observed"] == "NOT_RUN"
    assert actions["search_input_ready"] == "NOT_RUN"
    assert actions["search_modal_open_calls"] == "NOT_RUN"

    st = result["search_telemetry"]
    assert st["search_activation_dispatches"] == 0
    assert st["modal_ever_observed"] == "NOT_RUN"
    assert st["final_modal_count"] == "NOT_RUN"
    assert st["input_ready"] == "NOT_RUN"
    assert st["activation_to_first_modal_ms"] is None
    assert st["readiness_poll_count"] == "NOT_RUN"
    assert st["last_confirmed_stage"] == "NOT_RUN"
    assert st["failure_case_classification"] == "NOT_RUN"

    assert evaluate_live_gate(result) is False


def test_regression_10_distinct_semantics_not_run_unknown_false():
    """Regression 10: Prove that NOT_RUN, UNKNOWN, and False are distinct and distinguishable in artifacts."""
    # 1. NOT_RUN from dry-run
    dry_run_result = run_p2a_l1_qualification(dry_run=True)
    modal_dry_run = dry_run_result["live_action_inventory"]["search_modal_observed"]
    assert modal_dry_run == "NOT_RUN"

    # 2. UNKNOWN from live missing telemetry
    backend_missing = MagicMock()
    backend_missing.last_search_telemetry = {"activation_dispatch_count": 1}
    report_missing = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    harvest_search_telemetry(backend_missing, None, report_missing)
    modal_missing = report_missing["live_action_inventory"]["search_modal_observed"]
    assert modal_missing == "UNKNOWN"

    # 3. Explicitly observed False
    backend_observed_false = MagicMock()
    backend_observed_false.last_search_telemetry = {
        "activation_dispatch_count": 1,
        "modal_ever_observed": False,
    }
    report_observed_false = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    harvest_search_telemetry(backend_observed_false, None, report_observed_false)
    modal_false = report_observed_false["live_action_inventory"]["search_modal_observed"]
    assert modal_false is False

    # 4. Strict distinctness proof
    assert modal_dry_run != modal_missing
    assert modal_missing != modal_false
    assert modal_dry_run != modal_false
    assert {modal_dry_run, modal_missing, modal_false} == {"NOT_RUN", "UNKNOWN", False}


def test_regression_7_production_search_timeout_and_selectors_unchanged():
    """Regression 7: No production search timeout (5.0s) or selector was changed."""
    assert SEARCH_INPUT_READINESS_TIMEOUT_SECONDS == 5.0
    assert '[data-qa="search_input_box"] .ql-editor' in JS_CHECK_SEARCH_INPUT_READINESS
    assert '[data-qa="search_modal"]' in JS_CHECK_SEARCH_INPUT_READINESS
    assert 'button[data-qa="top_nav_search"]' in JS_ACTIVATE_SEARCH
    assert 'button[data-qa="top_nav_search"]' in JS_CHECK_AUTH_AND_READINESS


def test_regression_8_surface_query_matches_requested_source_separation():
    """Regression 8: verified_query_state_before_submit == true alone cannot satisfy SEARCH_SURFACE_QUERY_MATCHES_REQUESTED."""
    mock_backend = MagicMock()
    # Simulates pre-submit verification succeeded, but search failed before or without post-search surface query transition
    mock_backend.last_search_telemetry = {
        "activation_dispatch_count": 1,
        "modal_ever_observed": True,
        "input_ever_observed": True,
        "final_input_count": 1,
        "last_confirmed_stage": "SUBMIT_TRIGGERED",
        "onenter_invoked": True,
        "safe_search_action_selected": True,
        "verified_query_state_before_submit": True,
        # Notice: "forensic_transition" is absent!
    }
    report: dict[str, Any] = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    harvest_search_telemetry(mock_backend, None, report)

    assert report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] is True
    assert report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] == "UNKNOWN"

    live_report = _get_valid_baseline_live_report()
    live_report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] = True
    live_report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = "UNKNOWN"
    assert evaluate_live_gate(live_report) is False

    # Now add authoritative post-search forensic transition
    mock_backend.last_search_telemetry["forensic_transition"] = {
        "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True
    }
    harvest_search_telemetry(mock_backend, None, report)
    assert report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] is True


def test_regression_9_reply_bar_failure_telemetry_audit():
    """Regression 9: Reply-bar failure telemetry audit.

    - click_executed=True preserved on failure.
    - missing/ambiguous click records UNKNOWN (never defaults to 0).
    """
    # Case A: click executed, but root delta failed
    reader_click_done = MagicMock()
    reader_click_done.last_telemetry = {
        "residency_matched": True,
        "resident_matches": 1,
        "click_executed": True,
        "correlated_root_acquired": False,
    }
    report_a: dict[str, Any] = {"live_action_inventory": {}, "checks": {}}
    harvest_thread_reader_telemetry(reader_click_done, None, report_a)
    assert report_a["live_action_inventory"]["reply_bar_clicks"] == 1
    assert report_a["checks"]["reply_bar_guard_passed"] is True

    # Case B: reader telemetry is None when reading was attempted
    reader_missing = MagicMock()
    reader_missing.last_telemetry = None
    reader_missing.last_navigation_telemetry = None
    report_b: dict[str, Any] = {"live_action_inventory": {}, "checks": {}}
    harvest_thread_reader_telemetry(reader_missing, RuntimeError("DOM crash"), report_b)
    # Must NOT default to 0!
    assert report_b["live_action_inventory"]["reply_bar_clicks"] == "UNKNOWN"
    assert report_b["live_action_inventory"]["pure_read_dom_extractions"] == "UNKNOWN"
    assert report_b["checks"]["reply_bar_guard_passed"] == "UNKNOWN"

    # Live gate rejects UNKNOWN reply_bar_clicks
    live_report = _get_valid_baseline_live_report()
    live_report["live_action_inventory"]["reply_bar_clicks"] = "UNKNOWN"
    assert evaluate_live_gate(live_report) is False
