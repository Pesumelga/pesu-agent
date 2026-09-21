"""Task 11 / P2A-L1-RD1: Bounded Search Result Residency Diagnostic Offline Regression Suite (Run 2).

Tests purely offline:
- Telemetry harvesting: tri-state mapping (True / False / UNKNOWN) for all safety and query keys
- Pre-snapshot Query-Correlation & Safety Gate:
    - Explicit safety violations (generic onEnter, Slackbot, write-capable, unsafe non-search, unverified safe-action on submit) fail closed
    - Query mismatch fails closed
    - Missing authority fields yield SEARCH_RESULT_CAPTURE_INSUFFICIENT (NOT silently false)
    - Full compliance yields VERIFIED_SAFE and allows snapshot
- Disaggregated verdict reasons:
    - ZERO_MOUNTED_ROWS vs REQUIRED_SEARCH_TELEMETRY_MISSING vs SEARCH_QUERY_AUTHORITY_NOT_ESTABLISHED
- Invariant verification:
    - MAX_DIAGNOSTIC_ROWS = 20, SAME_SNAPSHOT_RESIDENCY_COUNTS = True
    - Immutable preservation of Run 1, Attempt 1, Attempt 2 artifacts
    - Distinct Run 2 live & dry-run paths
"""

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import scripts.diagnose_task11q4_p2a_l1_search_residency as diag_mod
from scripts.diagnose_task11q4_p2a_l1_search_residency import (
    DRY_RUN_OUTPUT_FILE,
    EXPECTED_CHANNEL_ID,
    EXPECTED_MESSAGE_TS,
    EXPECTED_ROOT_TS,
    LIVE_OUTPUT_FILE,
    MAX_DIAGNOSTIC_ROWS,
    PROTOCOL_FREEZE_PATH,
    PROTOCOL_NAME,
    RUN1_LIVE_TRACE_PATH,
    RUN1_LIVE_TRACE_SHA256,
    TARGET_QUERY,
    classify_row_identity,
    determine_diagnostic_verdict,
    evaluate_safety_and_query_authority,
    harvest_rd1_search_telemetry,
    run_rd1_diagnostic,
)


# =========================================================================
# 1. Telemetry Harvesting Regressions (Section J: 1-8, User Additions)
# =========================================================================

def test_1_safe_search_action_selected_true_maps_to_explicit_safe_true():
    """Test 1: Backend safe_search_action_selected=True maps to safe_search_action_explicitly_selected=True."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {"safe_search_action_selected": True}
    report = {}
    harvest_rd1_search_telemetry(mock_backend, None, report)
    assert report["safety_telemetry"]["safe_search_action_explicitly_selected"] is True


def test_2_safe_search_action_selected_false_maps_to_explicit_safe_false():
    """Test 2: Backend safe_search_action_selected=False maps to safe_search_action_explicitly_selected=False."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {"safe_search_action_selected": False}
    report = {}
    harvest_rd1_search_telemetry(mock_backend, None, report)
    assert report["safety_telemetry"]["safe_search_action_explicitly_selected"] is False


def test_3_missing_safe_action_field_maps_to_unknown_not_false():
    """Test 3: Missing safe_search_action_selected maps to UNKNOWN, never defaulting to False."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {}
    report = {}
    harvest_rd1_search_telemetry(mock_backend, None, report)
    assert report["safety_telemetry"]["safe_search_action_explicitly_selected"] == "UNKNOWN"
    assert report["safety_telemetry"]["safe_search_action_explicitly_selected"] is not False


def test_4_verified_query_state_before_submit_true_maps_correctly():
    """Test 4: Backend verified_query_state_before_submit=True maps to query_state_verified_before_submit=True."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {"verified_query_state_before_submit": True}
    report = {}
    harvest_rd1_search_telemetry(mock_backend, None, report)
    assert report["search_telemetry"]["query_state_verified_before_submit"] is True


def test_5_missing_pre_submit_verification_maps_to_unknown():
    """Test 5: Missing verified_query_state_before_submit maps to UNKNOWN, not False."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {}
    report = {}
    harvest_rd1_search_telemetry(mock_backend, None, report)
    assert report["search_telemetry"]["query_state_verified_before_submit"] == "UNKNOWN"
    assert report["search_telemetry"]["query_state_verified_before_submit"] is not False


def test_6_surface_query_match_harvested_from_forensic_transition():
    """Test 6: SEARCH_SURFACE_QUERY_MATCHES_REQUESTED is harvested from forensic_transition dict."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {
        "forensic_transition": {
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
            "SEARCH_SURFACE_QUERY_AFTER": "26년 하반기 간담회",
        }
    }
    report = {}
    harvest_rd1_search_telemetry(mock_backend, None, report)
    assert report["search_telemetry"]["search_surface_query_matches_requested"] is True
    assert report["search_telemetry"]["search_surface_query_after"] == "26년 하반기 간담회"


def test_7_surface_query_match_missing_maps_to_unknown():
    """Test 7: Missing forensic_transition or SEARCH_SURFACE_QUERY_MATCHES_REQUESTED maps to UNKNOWN."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {"forensic_transition": {}}
    report = {}
    harvest_rd1_search_telemetry(mock_backend, None, report)
    assert report["search_telemetry"]["search_surface_query_matches_requested"] == "UNKNOWN"
    assert report["search_telemetry"]["search_surface_query_after"] == "NOT_CAPTURED"


def test_8_actual_surface_query_string_preserved_when_present():
    """Test 8: SEARCH_SURFACE_QUERY_AFTER is preserved when present."""
    mock_backend = MagicMock()
    mock_backend.last_search_telemetry = {
        "forensic_transition": {
            "SEARCH_SURFACE_QUERY_AFTER": "26년 하반기 간담회",
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
        }
    }
    report = {}
    harvest_rd1_search_telemetry(mock_backend, None, report)
    assert report["search_telemetry"]["search_surface_query_after"] == "26년 하반기 간담회"


# =========================================================================
# 2. Safety & Query Authority Gate Regressions (Section J: 9-12, User Additions)
# =========================================================================

def test_9_submit_executed_alone_cannot_satisfy_query_authority():
    """Test 9: SUBMIT_EXECUTED alone without surface query match cannot satisfy query authority."""
    report = {
        "search_telemetry": {
            "last_confirmed_stage": "SUBMIT_EXECUTED",
            "search_submissions": 1,
            "query_state_verified_before_submit": True,
            "search_surface_query_matches_requested": "UNKNOWN",
        },
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": False,
            "composer_interaction": 0,
        },
    }
    safety_auth, query_auth, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert query_auth != "VERIFIED"
    assert verdict == "SEARCH_RESULT_CAPTURE_INSUFFICIENT"
    assert reason == "SEARCH_QUERY_AUTHORITY_NOT_ESTABLISHED"


def test_10_safe_and_pre_submit_true_but_surface_query_missing_inconclusive():
    """Test 10: Safe and pre-submit true but surface-query-match missing -> SEARCH_QUERY_AUTHORITY_NOT_ESTABLISHED."""
    report = {
        "search_telemetry": {
            "search_submissions": 1,
            "query_state_verified_before_submit": True,
            "search_surface_query_matches_requested": "UNKNOWN",
        },
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": False,
            "composer_interaction": 0,
        },
    }
    safety_auth, query_auth, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert verdict == "SEARCH_RESULT_CAPTURE_INSUFFICIENT"
    assert reason == "SEARCH_QUERY_AUTHORITY_NOT_ESTABLISHED"


def test_11_generic_default_onenter_true_fails_closed():
    """Test 11: generic_default_onenter_used=True fails closed with EXPLICIT_SEARCH_SAFETY_VIOLATION."""
    report = {
        "search_telemetry": {
            "search_submissions": 1,
            "query_state_verified_before_submit": True,
            "search_surface_query_matches_requested": True,
        },
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": True,
            "generic_default_onenter_used": True,  # VIOLATION
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": False,
            "composer_interaction": 0,
        },
    }
    safety_auth, query_auth, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert safety_auth == "VIOLATION_PROVEN"
    assert verdict == "FAIL_CLOSED"
    assert reason == "EXPLICIT_SEARCH_SAFETY_VIOLATION"


def test_12_query_authority_established_permits_snapshot():
    """Test 12: When all safety and query criteria are satisfied, gate passes and snapshot is permitted."""
    report = {
        "search_telemetry": {
            "search_submissions": 1,
            "query_state_verified_before_submit": True,
            "search_surface_query_matches_requested": True,
        },
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": False,
            "composer_interaction": 0,
        },
    }
    safety_auth, query_auth, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert safety_auth == "VERIFIED_SAFE"
    assert query_auth == "VERIFIED"
    assert verdict is None
    assert reason is None


def test_19_explicit_slackbot_action_true_fails_closed():
    """Test 19: slackbot_action_invoked_by_pesu=True fails closed."""
    report = {
        "search_telemetry": {"search_submissions": 1, "query_state_verified_before_submit": True, "search_surface_query_matches_requested": True},
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": True,  # VIOLATION
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": False,
            "composer_interaction": 0,
        },
    }
    safety_auth, _, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert safety_auth == "VIOLATION_PROVEN"
    assert verdict == "FAIL_CLOSED"
    assert reason == "EXPLICIT_SEARCH_SAFETY_VIOLATION"


def test_20_explicit_write_capable_action_true_fails_closed():
    """Test 20: write_capable_action_invoked_by_pesu=True fails closed."""
    report = {
        "search_telemetry": {"search_submissions": 1, "query_state_verified_before_submit": True, "search_surface_query_matches_requested": True},
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": True,  # VIOLATION
            "unsafe_non_search_action_invoked_by_pesu": False,
            "composer_interaction": 0,
        },
    }
    safety_auth, _, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert safety_auth == "VIOLATION_PROVEN"
    assert verdict == "FAIL_CLOSED"
    assert reason == "EXPLICIT_SEARCH_SAFETY_VIOLATION"


def test_21_explicit_unsafe_non_search_action_true_fails_closed():
    """Test 21: unsafe_non_search_action_invoked_by_pesu=True fails closed."""
    report = {
        "search_telemetry": {"search_submissions": 1, "query_state_verified_before_submit": True, "search_surface_query_matches_requested": True},
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": True,  # VIOLATION
            "composer_interaction": 0,
        },
    }
    safety_auth, _, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert safety_auth == "VIOLATION_PROVEN"
    assert verdict == "FAIL_CLOSED"
    assert reason == "EXPLICIT_SEARCH_SAFETY_VIOLATION"


def test_22_safety_field_missing_not_verified_insufficient():
    """Test 22: Missing safety field yields NOT_VERIFIED and REQUIRED_SEARCH_TELEMETRY_MISSING."""
    report = {
        "search_telemetry": {"search_submissions": 1, "query_state_verified_before_submit": True, "search_surface_query_matches_requested": True},
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": "UNKNOWN",  # MISSING
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": False,
            "composer_interaction": 0,
        },
    }
    safety_auth, _, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert safety_auth == "NOT_VERIFIED"
    assert verdict == "SEARCH_RESULT_CAPTURE_INSUFFICIENT"
    assert reason == "REQUIRED_SEARCH_TELEMETRY_MISSING"


def test_23_explicit_safe_action_false_after_submission_fails_closed():
    """Test 23: Submission occurred (submissions=1) but safe_action is False -> FAIL_CLOSED."""
    report = {
        "search_telemetry": {"search_submissions": 1, "query_state_verified_before_submit": True, "search_surface_query_matches_requested": True},
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": False,  # Submitted without safe action
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": False,
            "composer_interaction": 0,
        },
    }
    safety_auth, _, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert safety_auth == "VIOLATION_PROVEN"
    assert verdict == "FAIL_CLOSED"
    assert reason == "EXPLICIT_SEARCH_SAFETY_VIOLATION"


def test_24_all_required_safety_fields_compliant_verified_safe():
    """Test 24: All required safety fields compliant -> VERIFIED_SAFE."""
    report = {
        "search_telemetry": {"search_submissions": 1, "query_state_verified_before_submit": True, "search_surface_query_matches_requested": True},
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": False,
            "composer_interaction": 0,
        },
    }
    safety_auth, query_auth, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert safety_auth == "VERIFIED_SAFE"
    assert query_auth == "VERIFIED"
    assert verdict is None


def test_25_post_search_query_mismatch_fails_closed():
    """Test 25: Post-search surface query mismatch (matches=False) fails closed."""
    report = {
        "search_telemetry": {"search_submissions": 1, "query_state_verified_before_submit": True, "search_surface_query_matches_requested": False},
        "safety_telemetry": {
            "safe_search_action_explicitly_selected": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": False,
            "composer_interaction": 0,
        },
    }
    safety_auth, query_auth, verdict, reason = evaluate_safety_and_query_authority(report, None)
    assert query_auth == "QUERY_MISMATCH"
    assert verdict == "FAIL_CLOSED"
    assert reason == "SEARCH_QUERY_MISMATCH"


# =========================================================================
# 3. Disaggregated Reasons Regressions (Section J: 13-14)
# =========================================================================

def test_13_zero_mounted_rows_has_reason_zero_mounted_rows():
    """Test 13: 0 mounted rows has reason ZERO_MOUNTED_ROWS (distinct from missing telemetry)."""
    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[],
        mounted_row_count=0,
        inspected_row_count=0,
        inspection_truncated=False,
        telemetry_ok=True,
        same_snapshot_authority=True,
    )
    assert verdict == "SEARCH_RESULT_CAPTURE_INSUFFICIENT"
    assert reason == "ZERO_MOUNTED_ROWS"
    assert reason != "ZERO_MOUNTED_ROWS_OR_MISSING_TELEMETRY"


def test_14_missing_telemetry_has_distinct_reason():
    """Test 14: Missing telemetry has a different reason from zero rows."""
    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[],
        mounted_row_count=20,
        inspected_row_count=20,
        inspection_truncated=False,
        telemetry_ok=False,
        same_snapshot_authority=True,
        gate_failure_reason="REQUIRED_SEARCH_TELEMETRY_MISSING",
    )
    assert verdict == "SEARCH_RESULT_CAPTURE_INSUFFICIENT"
    assert reason == "REQUIRED_SEARCH_TELEMETRY_MISSING"
    assert reason != "ZERO_MOUNTED_ROWS"
    assert reason != "ZERO_MOUNTED_ROWS_OR_MISSING_TELEMETRY"


# =========================================================================
# 4. Artifact & Path Invariants (Section J: 15-18)
# =========================================================================

def test_15_run1_artifact_path_is_never_selected_for_run2():
    """Test 15: Run 1 artifact path is distinct from Run 2 live and dry-run paths."""
    assert LIVE_OUTPUT_FILE != RUN1_LIVE_TRACE_PATH
    assert DRY_RUN_OUTPUT_FILE != RUN1_LIVE_TRACE_PATH
    assert "run2" in LIVE_OUTPUT_FILE.name
    assert "run2" in DRY_RUN_OUTPUT_FILE.name


def test_16_run1_artifact_hash_remains_unchanged():
    """Test 16: Run 1 live artifact hash remains immutably 5A44F586..."""
    assert RUN1_LIVE_TRACE_PATH.exists(), f"Run 1 artifact missing: {RUN1_LIVE_TRACE_PATH}"
    h = hashlib.sha256(RUN1_LIVE_TRACE_PATH.read_bytes()).hexdigest().upper()
    assert h == RUN1_LIVE_TRACE_SHA256


def test_17_dry_run_action_counts_zero(tmp_path, monkeypatch):
    """Test 17: Dry-run performs zero UI actions and writes to dry-run output file."""
    tmp_out = tmp_path / "test_dry_run.json"
    monkeypatch.setattr(diag_mod, "DRY_RUN_OUTPUT_FILE", tmp_out)
    monkeypatch.setattr(diag_mod, "OUTPUT_DIR", tmp_path)

    report = run_rd1_diagnostic(dry_run=True)

    assert report["execution_mode"] == "DRY_RUN"
    assert report["diagnostic_run"] == "RD1_RUN_2"
    assert report["diagnostic_verdict"] == "DRY_RUN_PASS"
    assert report["SAME_SNAPSHOT_RESIDENCY_COUNTS"] is True
    st = report["safety_telemetry"]
    assert st["reply_bar_clicks"] == 0
    assert st["result_card_clicks"] == 0
    assert st["composer_interaction"] == 0
    assert st["scroll_iterations"] == 0
    assert st["provider_calls"] == 0
    assert tmp_out.exists()


def test_18_dry_run_cannot_claim_rd1_live_authority(tmp_path, monkeypatch):
    """Test 18: Dry-run marks safety and query authority as NOT_APPLICABLE_DRY_RUN."""
    tmp_out = tmp_path / "test_dry_run.json"
    monkeypatch.setattr(diag_mod, "DRY_RUN_OUTPUT_FILE", tmp_out)
    monkeypatch.setattr(diag_mod, "OUTPUT_DIR", tmp_path)

    report = run_rd1_diagnostic(dry_run=True)
    assert report["RD1_SEARCH_SAFETY_AUTHORITY"] == "NOT_APPLICABLE_DRY_RUN"
    assert report["RD1_RESULT_SET_QUERY_AUTHORITY"] == "NOT_APPLICABLE_DRY_RUN"
    assert report["thread_root_data_extraction_live_validated"] is False
    assert report["P2A_L1"] == "NOT_APPLICABLE_DIAGNOSTIC_ONLY"


# =========================================================================
# 5. Existing Tested Observer Invariants
# =========================================================================

def test_attempt1_and_attempt2_artifacts_unchanged():
    """Test: Attempt 1 and Attempt 2 trace artifacts remain untouched and uncorrupted."""
    repo_root = Path(__file__).resolve().parent.parent
    att1_path = repo_root / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_trace.json"
    att2_path = repo_root / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_attempt2_trace.json"

    assert att1_path.exists()
    assert att2_path.exists()

    def get_hash(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest().upper()

    assert get_hash(att1_path) == "F6F17E06F94808BAC15DEA32748526A1D146DAC69EF0A75CC8A4467C5C8FD28E"
    assert get_hash(att2_path) == "A6EACDB988306E610F446D65FD5EB56AE198D6CE976E62BD1937066EB834CD3A"


def test_row_identity_classification_exact_frozen_match():
    """Test: Row matching expected channel, message_ts, and root_ts is EXACT_FROZEN_IDENTITY_MATCH."""
    raw = {
        "raw_index": 1,
        "canonical_permalink": f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts={EXPECTED_ROOT_TS}",
        "qualifying_permalinks": [f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts={EXPECTED_ROOT_TS}"],
        "thread_reply_detected": True,
    }
    c = classify_row_identity(raw)
    assert c["target_classification"] == "EXACT_FROZEN_IDENTITY_MATCH"
    assert c["exact_frozen_match"] is True


def test_row_identity_classification_drift_match():
    """Test: Row matching channel and message_ts but different root_ts is SAME_CHANNEL_MESSAGE_DIFFERENT_THREAD."""
    raw = {
        "raw_index": 2,
        "canonical_permalink": f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts=1700000000.000000",
        "qualifying_permalinks": [f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts=1700000000.000000"],
        "thread_reply_detected": True,
    }
    c = classify_row_identity(raw)
    assert c["target_classification"] == "SAME_CHANNEL_MESSAGE_DIFFERENT_THREAD"
    assert c["same_channel_message_match"] is True
    assert c["exact_frozen_match"] is False


def test_row_identity_classification_different_message_same_thread():
    """Test: Row matching channel and root_ts but different message_ts is SAME_CHANNEL_THREAD_DIFFERENT_MESSAGE."""
    raw = {
        "raw_index": 3,
        "canonical_permalink": f"/archives/{EXPECTED_CHANNEL_ID}/p1700000000000000?thread_ts={EXPECTED_ROOT_TS}",
        "qualifying_permalinks": [f"/archives/{EXPECTED_CHANNEL_ID}/p1700000000000000?thread_ts={EXPECTED_ROOT_TS}"],
        "thread_reply_detected": True,
    }
    c = classify_row_identity(raw)
    assert c["target_classification"] == "SAME_CHANNEL_THREAD_DIFFERENT_MESSAGE"
    assert c["same_channel_thread_match"] is True
    assert c["exact_frozen_match"] is False


def test_row_identity_classification_unrelated():
    """Test: Row from different channel is UNRELATED."""
    raw = {
        "raw_index": 4,
        "canonical_permalink": "/archives/C9999999999/p1700000000000000",
        "qualifying_permalinks": ["/archives/C9999999999/p1700000000000000"],
    }
    c = classify_row_identity(raw)
    assert c["target_classification"] == "UNRELATED"
    assert c["exact_frozen_match"] is False
    assert c["same_channel_message_match"] is False
    assert c["same_channel_thread_match"] is False


def test_absence_verdict_requires_same_snapshot_authority():
    """Test: Absence verdict requires same_snapshot_authority == True."""
    unrelated_row = {
        "raw_index": 1,
        "canonical_permalink": "/archives/C9999999999/p1700000000000000",
        "qualifying_permalinks": ["/archives/C9999999999/p1700000000000000"],
    }
    c_unrelated = classify_row_identity(unrelated_row)

    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[c_unrelated],
        mounted_row_count=1,
        inspected_row_count=1,
        inspection_truncated=False,
        telemetry_ok=True,
        same_snapshot_authority=False,
    )
    assert verdict == "SEARCH_RESULT_CAPTURE_INSUFFICIENT"
    assert reason == "LACKS_SAME_SNAPSHOT_COUNT_AUTHORITY"


def test_truncation_bound_triggers_insufficient():
    """Test: Truncation bound (mounted > inspected) triggers MOUNTED_RESULT_SET_TRUNCATED_BY_DIAGNOSTIC_BOUND."""
    unrelated_row = {
        "raw_index": 1,
        "canonical_permalink": "/archives/C9999999999/p1700000000000000",
        "qualifying_permalinks": ["/archives/C9999999999/p1700000000000000"],
    }
    c_unrelated = classify_row_identity(unrelated_row)

    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[c_unrelated] * 20,
        mounted_row_count=25,
        inspected_row_count=20,
        inspection_truncated=True,
        telemetry_ok=True,
        same_snapshot_authority=True,
    )
    assert verdict == "SEARCH_RESULT_CAPTURE_INSUFFICIENT"
    assert reason == "MOUNTED_RESULT_SET_TRUNCATED_BY_DIAGNOSTIC_BOUND"
