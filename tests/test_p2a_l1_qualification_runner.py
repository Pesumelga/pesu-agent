"""Regression tests for Task 11 / P2A-L1 Qualification Runner.

Tests required by protocol hardening:
- Regression A: Dry-run passes all static checks, but THREAD_ROOT_DATA_EXTRACTION_LIVE_VALIDATED == false.
- Regression B: Live execution evidence missing -> P2A_L1 != PASS.
- Regression C: Synthetic success values in dry-run cannot lead to live PASS.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from scripts.qualify_task11q4_p2a_live_root_extraction import (
    evaluate_live_gate,
    run_p2a_l1_qualification,
)


def test_regression_a_dry_run_never_declares_live_pass():
    """Regression A: Dry-run must return DRY_RUN_PASS, but THREAD_ROOT_DATA_EXTRACTION_LIVE_VALIDATED must be False."""
    result = run_p2a_l1_qualification(dry_run=True)

    assert result["verdict"] == "DRY_RUN_PASS"
    assert result["P2A_L1"] == "NOT_EXECUTED"
    assert result["thread_root_data_extraction_live_validated"] is False
    assert result["live_action_inventory"]["search_modal_open_calls"] == 0
    assert result["live_action_inventory"]["search_submissions"] == 0
    assert result["live_action_inventory"]["reply_bar_clicks"] == 0
    assert result["live_action_inventory"]["pure_read_dom_extractions"] == 0


def test_regression_b_missing_live_evidence_fails_closed():
    """Regression B: Missing any live evidence must cause evaluate_live_gate to return False."""
    mock_report = {
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
            "search_submissions": 1,
            "reply_bar_clicks": 1,
            "pure_read_dom_extractions": 1,
            "new_ui_actions_added": 0,
            "new_click_targets_added": 0,
        },
        "provider_calls": 0,
        "retry_occurred": False,
    }

    # Baseline all valid -> True
    assert evaluate_live_gate(mock_report) is True

    # 1. Missing SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED
    bad_report = json.loads(json.dumps(mock_report))
    bad_report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = False
    assert evaluate_live_gate(bad_report) is False

    # 2. Missing SEARCH_SURFACE_QUERY_MATCHES_REQUESTED
    bad_report = json.loads(json.dumps(mock_report))
    bad_report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = False
    assert evaluate_live_gate(bad_report) is False

    # 3. reply_bar_clicks != 1
    bad_report = json.loads(json.dumps(mock_report))
    bad_report["live_action_inventory"]["reply_bar_clicks"] = 0
    assert evaluate_live_gate(bad_report) is False

    bad_report["live_action_inventory"]["reply_bar_clicks"] = 2
    assert evaluate_live_gate(bad_report) is False

    # 4. live_execution_completed is False
    bad_report = json.loads(json.dumps(mock_report))
    bad_report["live_execution_completed"] = False
    assert evaluate_live_gate(bad_report) is False

    # 5. root_extraction_result_source != LIVE_DOM
    bad_report = json.loads(json.dumps(mock_report))
    bad_report["checks"]["root_extraction_result_source"] = "SYNTHETIC_FIXTURE"
    assert evaluate_live_gate(bad_report) is False


def test_regression_c_synthetic_dry_run_cannot_pass_live_gate():
    """Regression C: Injected synthetic action counters or dry_run=True cannot satisfy live gate."""
    # Even if execution_mode is DRY_RUN with all checks mocked as True:
    dry_run_report = {
        "execution_mode": "DRY_RUN",
        "live_execution_started": False,
        "live_execution_completed": False,
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
            "search_submissions": 1,
            "reply_bar_clicks": 1,
            "pure_read_dom_extractions": 1,
            "new_ui_actions_added": 0,
            "new_click_targets_added": 0,
        },
        "provider_calls": 0,
        "retry_occurred": False,
    }
    # evaluate_live_gate MUST reject non-LIVE execution mode
    assert evaluate_live_gate(dry_run_report) is False
