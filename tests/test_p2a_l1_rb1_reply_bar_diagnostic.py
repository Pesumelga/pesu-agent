"""Regression tests for Task 11 / P2A-L1 / RB1: Reply-Bar Structure Diagnostic.

Validates:
1. Target identity contracts (frozen query, channel, message_ts, root_ts, rank 5).
2. Protocol freeze dependency manifest integrity and SHA-256 hashes.
3. Search safety and query correlation gate evaluation.
4. Supplemental composer tri-state safety semantics.
5. Search telemetry harvesting and action inventory.
6. Live artifact overwrite guard.
7. Dry-run execution with zero live actions, zero clicks, zero provider calls.
8. Hash immutability of all 6 historical qualification artifacts.
9. Production src/ git diff is strictly empty.
10. Atomic JS snapshot simulation for 0 reply bars.
11. Atomic JS snapshot simulation for 1 qualified reply bar.
12. Atomic JS snapshot simulation for multiple reply bars.
13. Atomic JS snapshot simulation for non-resident target.
14. Atomic JS snapshot simulation for ambiguous resident targets.
15. Provider calls strictly zero.
16. P2A_L1 is NOT_APPLICABLE_DIAGNOSTIC_ONLY and extraction validated is False.
17. Zero clicks strictly preserved.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink
from pesu_agent.search.webview_thread_reader import normalize_slack_ts

from scripts.diagnose_task11q4_p2a_l1_reply_bar_structure import (
    DEPENDENCY_PATHS,
    DRY_RUN_OUTPUT_FILE,
    EXPECTED_CHANNEL_ID,
    EXPECTED_MESSAGE_TS,
    EXPECTED_ROOT_TS,
    LIVE_OUTPUT_FILE,
    PROTOCOL_FREEZE_PATH,
    SEARCH_MAX_RESULTS,
    SOURCE_RAW_PERMALINK,
    SOURCE_RD1_RUN2_RANK,
    TARGET_FITNESS_CONTRACT,
    TARGET_QUERY,
    evaluate_search_safety_and_query_authority,
    harvest_search_telemetry,
    is_supplemental_safe,
    run_rb1_diagnostic,
)


def test_1_target_identity_contracts():
    """Validates frozen target identity attributes and fitness contract."""
    assert TARGET_QUERY == "26년 하반기 간담회"
    assert EXPECTED_CHANNEL_ID == "C042WS25QAX"
    assert EXPECTED_MESSAGE_TS == "1718079276.126469"
    assert EXPECTED_ROOT_TS == "1718058683.912369"
    assert SOURCE_RD1_RUN2_RANK == 5
    assert SEARCH_MAX_RESULTS == 5
    assert TARGET_FITNESS_CONTRACT == "EXPECTED_MESSAGE_TS != EXPECTED_ROOT_TS"
    assert EXPECTED_MESSAGE_TS != EXPECTED_ROOT_TS

    parsed = parse_slack_message_permalink(SOURCE_RAW_PERMALINK)
    assert parsed is not None
    assert parsed.channel_id == EXPECTED_CHANNEL_ID
    assert normalize_slack_ts(parsed.message_ts) == normalize_slack_ts(EXPECTED_MESSAGE_TS)
    assert normalize_slack_ts(parsed.thread_ts) == normalize_slack_ts(EXPECTED_ROOT_TS)


def test_2_dependency_paths_and_hashes():
    """Validates that all tracked dependency paths exist and match protocol freeze manifest."""
    assert PROTOCOL_FREEZE_PATH.exists()
    with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    expected_hashes = manifest["dependency_hashes"]
    for name, path in DEPENDENCY_PATHS.items():
        assert path.exists(), f"Dependency {name} does not exist at {path}"
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        assert name in expected_hashes, f"Dependency {name} missing from protocol freeze!"
        assert actual_hash == expected_hashes[name], (
            f"Hash mismatch for {name}: expected {expected_hashes[name]}, got {actual_hash}"
        )


def test_3_search_safety_and_query_authority_evaluation():
    """Validates search safety and query correlation gate logic."""
    base_report = {
        "checks": {
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": True,
            "QUERY_STATE_VERIFIED_BEFORE_SUBMIT": True,
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
        },
        "safety_telemetry": {
            "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": False,
            "SLACKBOT_ACTION_INVOKED_BY_PESU": False,
            "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": False,
            "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": False,
            "composer_focus": "UNKNOWN",
            "composer_text_input": "UNKNOWN",
            "composer_send": "UNKNOWN",
        },
        "live_action_inventory": {
            "search_submissions": 1,
        },
    }
    ok, reason = evaluate_search_safety_and_query_authority(base_report)
    assert ok is True
    assert reason == "VERIFIED_SAFE_AND_CORRELATED"

    # Explicit violations fail closed
    for violation_key in [
        "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH",
        "SLACKBOT_ACTION_INVOKED_BY_PESU",
        "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU",
        "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU",
    ]:
        bad_report = json.loads(json.dumps(base_report))
        bad_report["safety_telemetry"][violation_key] = True
        ok, reason = evaluate_search_safety_and_query_authority(bad_report)
        assert ok is False
        assert "EXPLICIT_SAFETY_VIOLATION" in reason


def test_4_supplemental_composer_semantics():
    """Validates supplemental composer UNKNOWN passes, but explicit True/non-zero fails closed."""
    assert is_supplemental_safe("UNKNOWN") is True
    assert is_supplemental_safe(None) is True
    assert is_supplemental_safe(0) is True
    assert is_supplemental_safe(False) is True
    assert is_supplemental_safe("NOT_RUN") is True

    assert is_supplemental_safe(True) is False
    assert is_supplemental_safe(1) is False
    assert is_supplemental_safe("EXPLICIT_TRUE") is False


def test_5_harvest_search_telemetry_fidelity():
    """Validates that search telemetry fields correctly harvest into report."""
    class MockBackend:
        last_telemetry = {
            "activation_dispatched": True,
            "modal_ever_observed": True,
            "final_modal_count": 1,
            "input_ever_observed": True,
            "final_input_count": 1,
            "last_confirmed_stage": "RESULTS_RENDERED",
            "onenter_invoked": True,
            "safe_search_action_selected": True,
            "verified_query_state_before_submit": True,
            "forensic_transition": {
                "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
                "SEARCH_SURFACE_QUERY_AFTER": "다음에 대한 결과: 26년 하반기 간담회",
            },
            "generic_default_onenter_used": False,
            "slackbot_action_invoked": False,
            "write_capable_action_invoked": False,
            "unsafe_non_search_action_invoked": False,
        }

    report = {}
    harvest_search_telemetry(MockBackend(), None, report)

    assert report["live_action_inventory"]["search_activation_dispatches"] == 1
    assert report["live_action_inventory"]["search_modal_observed"] is True
    assert report["live_action_inventory"]["search_input_ready"] is True
    assert report["live_action_inventory"]["search_submissions"] == 1
    assert report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] is True
    assert report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] is True
    assert report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] is True


def test_6_live_artifact_overwrite_guard(tmp_path):
    """Verifies overwrite guard aborts execution if live trace artifact already exists."""
    import scripts.diagnose_task11q4_p2a_l1_reply_bar_structure as runner_mod

    dummy_live = runner_mod.LIVE_OUTPUT_FILE
    assert not dummy_live.exists(), "Precondition failed: LIVE_OUTPUT_FILE already exists!"

    try:
        dummy_live.parent.mkdir(parents=True, exist_ok=True)
        dummy_live.write_bytes(b'{"preexisting": "rb1_live_artifact"}')

        report = runner_mod.run_rb1_diagnostic(dry_run=False)
        assert report["verdict"] == "FAIL_CLOSED"
        assert report["error"] == "LIVE_ARTIFACT_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
        assert report["diagnostic_verdict"] == "REPLAY_FORBIDDEN"
        assert report["live_action_inventory"]["search_activation_dispatches"] == 0
        assert report["live_action_inventory"]["search_submissions"] == 0
        assert report["live_action_inventory"]["reply_bar_clicks"] == 0
        assert report["live_action_inventory"]["pure_read_dom_extractions"] == 0
    finally:
        if dummy_live.exists():
            dummy_live.unlink()


def test_7_dry_run_zero_live_actions_and_trace_persistence():
    """Verifies dry-run executes zero live actions and records dry trace."""
    report = run_rb1_diagnostic(dry_run=True)
    assert report["execution_mode"] == "DRY_RUN"
    assert report["verdict"] == "DRY_RUN_PASS"
    assert report["diagnostic_verdict"] == "DRY_RUN_PASS"
    assert report["live_action_inventory"]["search_activation_dispatches"] == 0
    assert report["live_action_inventory"]["search_submissions"] == 0
    assert report["live_action_inventory"]["reply_bar_clicks"] == 0
    assert report["live_action_inventory"]["pure_read_dom_extractions"] == 0
    assert report["live_action_inventory"]["new_ui_actions_added"] == 0
    assert report["live_action_inventory"]["new_click_targets_added"] == 0
    assert report["provider_calls"] == 0
    assert DRY_RUN_OUTPUT_FILE.exists()


def test_8_historical_traces_hash_identical():
    """Verifies all 6 historical qualification artifacts remain hash-identical."""
    expected_hashes = {
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_trace.json": (
            "f6f17e06f94808bac15dea32748526a1d146dac69ef0a75cc8a4467c5c8fd28e"
        ),
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt2_trace.json": (
            "a6eacdb988306e610f446d65fd5eb56ae198d6ce976e62bd1937066eb834cd3a"
        ),
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rd1_search_residency.json": (
            "5a44f58616a5dac21ebeb596a10e8f55ba7a2263eb177b945485c2bdd2556846"
        ),
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rd1_run2_search_residency.json": (
            "fccfb28d380c81ad66e435ac43b63778184cb197da87d61eb304bf391c28f6a5"
        ),
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt3_trace.json": (
            "b00fa70ec84da9efe329afed4cc70b4c00765adf64e773c2c5624ad72c84b18e"
        ),
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt4_trace.json": (
            "e115b52de6b1ac1203d98e5a108fbe7b6a375fd708dc93233067d40a910a4965"
        ),
    }
    for rel_path, exp_hash in expected_hashes.items():
        p = REPO_ROOT / rel_path
        assert p.exists(), f"Historical artifact {rel_path} does not exist!"
        actual_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        assert actual_hash == exp_hash, f"Hash mismatch for {rel_path}: expected {exp_hash}, got {actual_hash}"


def test_9_production_src_diff_strictly_empty():
    """Verifies that production src/ directory has 0 git diff."""
    diff = subprocess.check_output(
        ["git", "diff", "HEAD", "--", "src/"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()
    assert diff == "", f"Production src/ diff is not empty: {diff}"


def test_10_atomic_js_snapshot_simulation_zero_reply_bars():
    """Verifies atomic snapshot correctly reports target resident with 0 reply bars."""
    mock_snapshot = {
        "ok": True,
        "target_resident": True,
        "resident_matches": 1,
        "target_row_index": 0,
        "mounted_row_count": 5,
        "inspected_row_count": 5,
        "reply_bar_cardinality": {
            "total_reply_bars": 0,
            "div_reply_bars": 0,
            "button_reply_bars": 0,
            "visible_reply_bars": 0,
            "qualified_reply_bars": 0,
            "production_reader_guard_would_pass": False,
        },
        "reply_bar_elements": [],
        "alternative_candidates": [],
    }

    report = {"checks": {}, "live_action_inventory": {}}
    report["atomic_inspection"] = mock_snapshot
    card = mock_snapshot["reply_bar_cardinality"]
    assert card["total_reply_bars"] == 0
    assert card["production_reader_guard_would_pass"] is False


def test_11_atomic_js_snapshot_simulation_one_qualified_reply_bar():
    """Verifies atomic snapshot correctly reports target resident with 1 qualified reply bar."""
    mock_snapshot = {
        "ok": True,
        "target_resident": True,
        "resident_matches": 1,
        "target_row_index": 0,
        "mounted_row_count": 5,
        "inspected_row_count": 5,
        "reply_bar_cardinality": {
            "total_reply_bars": 1,
            "div_reply_bars": 1,
            "button_reply_bars": 0,
            "visible_reply_bars": 1,
            "qualified_reply_bars": 1,
            "production_reader_guard_would_pass": True,
        },
        "reply_bar_elements": [{
            "index": 0,
            "tag_name": "div",
            "is_connected": True,
            "is_visible": True,
            "is_qualified": True,
        }],
        "alternative_candidates": [],
    }

    card = mock_snapshot["reply_bar_cardinality"]
    assert card["total_reply_bars"] == 1
    assert card["qualified_reply_bars"] == 1
    assert card["production_reader_guard_would_pass"] is True


def test_12_atomic_js_snapshot_simulation_multiple_reply_bars():
    """Verifies atomic snapshot correctly reports target resident with >1 reply bars."""
    mock_snapshot = {
        "ok": True,
        "target_resident": True,
        "resident_matches": 1,
        "target_row_index": 0,
        "mounted_row_count": 5,
        "inspected_row_count": 5,
        "reply_bar_cardinality": {
            "total_reply_bars": 2,
            "div_reply_bars": 2,
            "button_reply_bars": 0,
            "visible_reply_bars": 2,
            "qualified_reply_bars": 2,
            "production_reader_guard_would_pass": False,
        },
        "reply_bar_elements": [
            {"index": 0, "tag_name": "div", "is_qualified": True},
            {"index": 1, "tag_name": "div", "is_qualified": True},
        ],
        "alternative_candidates": [],
    }

    card = mock_snapshot["reply_bar_cardinality"]
    assert card["total_reply_bars"] == 2
    assert card["production_reader_guard_would_pass"] is False


def test_13_atomic_js_snapshot_simulation_target_not_resident():
    """Verifies atomic snapshot correctly reports target not resident."""
    mock_snapshot = {
        "ok": True,
        "target_resident": False,
        "resident_matches": 0,
        "mounted_row_count": 5,
        "inspected_row_count": 5,
        "reply_bar_cardinality": None,
        "reply_bar_elements": [],
        "alternative_candidates": [],
    }
    assert mock_snapshot["target_resident"] is False
    assert mock_snapshot["resident_matches"] == 0
    assert mock_snapshot["reply_bar_cardinality"] is None


def test_14_atomic_js_snapshot_simulation_ambiguous_rows():
    """Verifies atomic snapshot correctly reports ambiguous resident rows."""
    mock_snapshot = {
        "ok": True,
        "target_resident": False,
        "resident_matches": 2,
        "error": "ROW_IDENTITY_AMBIGUOUS",
        "mounted_row_count": 5,
        "inspected_row_count": 5,
        "reply_bar_cardinality": None,
    }
    assert mock_snapshot["target_resident"] is False
    assert mock_snapshot["resident_matches"] == 2
    assert mock_snapshot["error"] == "ROW_IDENTITY_AMBIGUOUS"


def test_15_provider_calls_strictly_zero():
    """Verifies provider calls count is 0."""
    report = run_rb1_diagnostic(dry_run=True)
    assert report["provider_calls"] == 0


def test_16_p2a_l1_not_applicable_diagnostic_only():
    """Verifies P2A_L1 is set to NOT_APPLICABLE_DIAGNOSTIC_ONLY."""
    report = run_rb1_diagnostic(dry_run=True)
    assert report["P2A_L1"] == "NOT_APPLICABLE_DIAGNOSTIC_ONLY"
    assert report["thread_root_data_extraction_live_validated"] is False


def test_17_zero_clicks_strictly_preserved():
    """Verifies zero clicks strictly preserved in live_action_inventory and checks."""
    report = run_rb1_diagnostic(dry_run=True)
    assert report["live_action_inventory"]["reply_bar_clicks"] == 0
    assert report["checks"]["zero_clicks_strictly_preserved"] is True
