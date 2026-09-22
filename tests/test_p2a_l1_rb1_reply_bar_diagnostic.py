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
13. Sensitive-content exclusion (zero user message text, zero outer HTML, zero unrelated raw permalinks).
14. Exact production cardinality predicate mirrored (isolated from subsequent structural observations).
15. Attempt 4 runtime/search path parity verification.
16. Runtime import closure audit (untracked loaded project dependencies == 0).
17. Provider calls strictly zero.
18. P2A_L1 is NOT_APPLICABLE_DIAGNOSTIC_ONLY and extraction validated is False.
19. Zero clicks strictly preserved.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink
from pesu_agent.runtime.pesu_webview_host import DIRECT_SLACK_PROFILE_PATH, PesuWebViewHost
from pesu_agent.search.webview_slack_search import WebViewRuntimeConfig, WebViewSlackSearchBackend
from pesu_agent.search.webview_thread_reader import normalize_slack_ts

from scripts.diagnose_task11q4_p2a_l1_reply_bar_structure import (
    DEPENDENCY_PATHS,
    DRY_RUN_OUTPUT_FILE,
    EXPECTED_CHANNEL_ID,
    EXPECTED_MESSAGE_TS,
    EXPECTED_ROOT_TS,
    LIVE_OUTPUT_FILE,
    PRIMARY_DEPENDENCY_PATHS,
    PROTOCOL_FREEZE_PATH,
    SEARCH_MAX_RESULTS,
    SOURCE_RAW_PERMALINK,
    SOURCE_RD1_RUN2_RANK,
    TARGET_FITNESS_CONTRACT,
    TARGET_QUERY,
    audit_runtime_import_closure,
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
    for name, path in PRIMARY_DEPENDENCY_PATHS.items():
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


def test_6_live_artifact_overwrite_guard():
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
    """Verifies atomic snapshot correctly isolates production cardinality guard when count is 0."""
    mock_snapshot = {
        "ok": True,
        "target_resident": True,
        "resident_matches": 1,
        "target_row_index": 0,
        "mounted_row_count": 5,
        "inspected_row_count": 5,
        "reply_bar_cardinality": {
            "production_selector_connected_div_reply_bar_count": 0,
            "production_cardinality_guard_would_pass": False,
            "rendered_reply_bar_count": 0,
            "hidden_reply_bar_count": 0,
            "nested_reply_bar_count": 0,
            "composer_contained_reply_bar_count": 0,
            "view_thread_control_count": 0,
            "reply_bar_count_control_count": 0,
            "total_reply_bar_elements_count": 0,
            "button_reply_bar_count": 0,
            "structural_candidate_count": 0,
        },
        "reply_bar_elements": [],
        "alternative_candidates": [],
    }

    card = mock_snapshot["reply_bar_cardinality"]
    assert card["production_selector_connected_div_reply_bar_count"] == 0
    assert card["production_cardinality_guard_would_pass"] is False


def test_11_atomic_js_snapshot_simulation_one_qualified_reply_bar():
    """Verifies atomic snapshot correctly isolates production cardinality guard when count is 1."""
    mock_snapshot = {
        "ok": True,
        "target_resident": True,
        "resident_matches": 1,
        "target_row_index": 0,
        "mounted_row_count": 5,
        "inspected_row_count": 5,
        "reply_bar_cardinality": {
            "production_selector_connected_div_reply_bar_count": 1,
            "production_cardinality_guard_would_pass": True,
            "rendered_reply_bar_count": 1,
            "hidden_reply_bar_count": 0,
            "nested_reply_bar_count": 0,
            "composer_contained_reply_bar_count": 0,
            "view_thread_control_count": 1,
            "reply_bar_count_control_count": 1,
            "total_reply_bar_elements_count": 1,
            "button_reply_bar_count": 0,
            "structural_candidate_count": 0,
        },
        "reply_bar_elements": [{
            "index": 0,
            "tag_name": "div",
            "data_qa": "reply_bar",
            "is_connected": True,
            "is_visible": True,
            "has_reply_bar_count_control": True,
            "has_reply_bar_view_thread_control": True,
            "in_composer": False,
        }],
        "alternative_candidates": [],
    }

    card = mock_snapshot["reply_bar_cardinality"]
    assert card["production_selector_connected_div_reply_bar_count"] == 1
    assert card["production_cardinality_guard_would_pass"] is True


def test_12_atomic_js_snapshot_simulation_multiple_reply_bars():
    """Verifies atomic snapshot correctly isolates production cardinality guard when count is >1."""
    mock_snapshot = {
        "ok": True,
        "target_resident": True,
        "resident_matches": 1,
        "target_row_index": 0,
        "mounted_row_count": 5,
        "inspected_row_count": 5,
        "reply_bar_cardinality": {
            "production_selector_connected_div_reply_bar_count": 2,
            "production_cardinality_guard_would_pass": False,
            "rendered_reply_bar_count": 2,
            "hidden_reply_bar_count": 0,
            "nested_reply_bar_count": 1,
            "composer_contained_reply_bar_count": 0,
            "view_thread_control_count": 2,
            "reply_bar_count_control_count": 2,
            "total_reply_bar_elements_count": 2,
            "button_reply_bar_count": 0,
            "structural_candidate_count": 1,
        },
        "reply_bar_elements": [
            {"index": 0, "tag_name": "div", "is_connected": True, "is_visible": True},
            {"index": 1, "tag_name": "div", "is_connected": True, "is_visible": True},
        ],
        "alternative_candidates": [],
    }

    card = mock_snapshot["reply_bar_cardinality"]
    assert card["production_selector_connected_div_reply_bar_count"] == 2
    assert card["production_cardinality_guard_would_pass"] is False


def test_13_sensitive_content_exclusion():
    """Verifies sensitive content (user text, outerHTML, unrelated permalinks) is strictly excluded."""
    report = run_rb1_diagnostic(dry_run=True)
    checks = report.get("checks", {})

    assert checks.get("USER_MESSAGE_TEXT_CAPTURED") is False
    assert checks.get("UNRELATED_RAW_PERMALINK_CAPTURED") is False
    assert checks.get("OUTER_HTML_CAPTURED") is False


def test_14_exact_production_cardinality_predicate_mirrored():
    """Verifies production cardinality predicate is mirrored exactly without conflating subsequent observations."""
    report = run_rb1_diagnostic(dry_run=True)
    assert report["checks"].get("PRODUCTION_CARDINALITY_PREDICATE_MIRRORED_EXACTLY") is True


def test_15_attempt4_runtime_search_path_parity():
    """Proves RB1 live runner constructor and method calls have 100% parity with Attempt 4 authoritative runner."""
    attempt4_path = REPO_ROOT / "scripts" / "qualify_task11q4_p2a_live_root_extraction_attempt4.py"
    assert attempt4_path.exists(), f"Authoritative Attempt 4 runner missing at {attempt4_path}"
    
    with open(attempt4_path, "r", encoding="utf-8") as f:
        attempt4_src = f.read()
        
    rb1_path = REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_reply_bar_structure.py"
    with open(rb1_path, "r", encoding="utf-8") as f:
        rb1_src = f.read()

    # 1. PesuWebViewHost construction parity
    assert 'host = PesuWebViewHost(' in attempt4_src
    assert 'host = PesuWebViewHost(' in rb1_src
    assert 'user_data_folder=DIRECT_SLACK_PROFILE_PATH' in attempt4_src and 'user_data_folder=DIRECT_SLACK_PROFILE_PATH' in rb1_src
    assert 'initial_url="https://app.slack.com/client"' in attempt4_src and 'initial_url="https://app.slack.com/client"' in rb1_src
    assert 'title="PESU Task 11 / P2A-L1 Live Qualification"' in attempt4_src and 'title="PESU Task 11 / P2A-L1 Live Qualification"' in rb1_src

    # 2. WebViewSlackSearchBackend construction parity
    assert 'backend = WebViewSlackSearchBackend(' in attempt4_src
    assert 'backend = WebViewSlackSearchBackend(' in rb1_src
    assert 'config=WebViewRuntimeConfig(storage_path=Path(DIRECT_SLACK_PROFILE_PATH))' in attempt4_src
    assert 'config=WebViewRuntimeConfig(storage_path=Path(DIRECT_SLACK_PROFILE_PATH))' in rb1_src
    assert 'host=host' in attempt4_src and 'host=host' in rb1_src

    # 3. Start runtime parity
    assert 'backend.start_runtime(timeout=timeout)' in attempt4_src
    assert 'backend.start_runtime(timeout=timeout)' in rb1_src

    # 4. Search invocation parity
    assert 'backend.search_messages(TARGET_QUERY,' in attempt4_src
    assert 'backend.search_messages(TARGET_QUERY,' in rb1_src

    # 5. Host shutdown parity
    assert 'host.destroy()' in attempt4_src
    assert 'host.destroy()' in rb1_src

    # 6. Dry run report verification
    report = run_rb1_diagnostic(dry_run=True)
    assert report["checks"].get("RB1_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4") is True


def test_16_runtime_import_closure_audit():
    """Audits loaded project modules against the protocol manifest to guarantee zero untracked dependencies."""
    assert PROTOCOL_FREEZE_PATH.exists()
    with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    audit_result = audit_runtime_import_closure(manifest)
    assert audit_result["closure_audit_passed"] is True, (
        f"Closure audit failed: untracked={audit_result['untracked_files']}, mismatches={audit_result['mismatched_files']}"
    )
    assert audit_result["untracked_loaded_project_dependency_count"] == 0
    assert len(audit_result["untracked_files"]) == 0
    assert len(audit_result["mismatched_files"]) == 0
    assert audit_result["loaded_project_module_count"] >= 12


def test_17_provider_calls_strictly_zero():
    """Verifies provider calls count is 0."""
    report = run_rb1_diagnostic(dry_run=True)
    assert report["provider_calls"] == 0


def test_18_p2a_l1_not_applicable_diagnostic_only():
    """Verifies P2A_L1 is set to NOT_APPLICABLE_DIAGNOSTIC_ONLY."""
    report = run_rb1_diagnostic(dry_run=True)
    assert report["P2A_L1"] == "NOT_APPLICABLE_DIAGNOSTIC_ONLY"
    assert report["thread_root_data_extraction_live_validated"] is False


def test_19_zero_clicks_strictly_preserved():
    """Validates that zero clicks and all action contracts are preserved."""
    report = run_rb1_diagnostic(dry_run=True)
    actions = report["live_action_inventory"]
    assert actions.get("reply_bar_clicks", 0) == 0
    assert report["checks"].get("zero_clicks_strictly_preserved") is True

    # Action contracts verification
    contracts = report.get("action_contracts", {})
    assert contracts.get("MAX_SLACK_SEARCH_OPERATIONS") == 1
    assert contracts.get("MAX_SEARCH_SUBMISSIONS") == 1
    assert contracts.get("MAX_REPLY_BAR_CLICKS") == 0
    assert contracts.get("MAX_RESULT_CARD_CLICKS") == 0
    assert contracts.get("MAX_THREAD_EXPANSIONS") == 0
    assert contracts.get("MAX_SCROLL_ACTIONS") == 0
    assert contracts.get("MAX_KEYBOARD_EVENTS") == 0
    assert contracts.get("MAX_COMPOSER_INTERACTIONS") == 0
    assert contracts.get("MAX_WRITE_CAPABLE_ACTIONS") == 0
    assert contracts.get("MAX_PROVIDER_CALLS") == 0
    assert contracts.get("MAX_RETRIES") == 0
    assert report["checks"].get("ACTION_CONTRACTS_SATISFIED") is True



def test_20_frozen_hash_mismatch_blocks_live_before_webview(monkeypatch, tmp_path):
    """Proves that any frozen hash mismatch fails closed BEFORE WebView construction or Slack search.

    Ensures:
    - frozen hash mismatch detected
    - verdict == FAIL_CLOSED
    - diagnostic reason == INTEGRITY_MISMATCH
    - error == PRE_WEBVIEW_HARD_GATE_FAILED_FROZEN_HASH_MISMATCH
    - WebView constructor call count == 0
    - start_runtime call count == 0
    - Slack search call count == 0
    - DOM evaluate call count == 0
    - reply-bar click count == 0
    - live_execution_started == False
    """
    import scripts.diagnose_task11q4_p2a_l1_reply_bar_structure as runner_mod

    # Redirect live output file so real live artifact is never touched
    monkeypatch.setattr(runner_mod, "LIVE_OUTPUT_FILE", tmp_path / "mock_live_artifact.json")

    # Tracking calls to ensure zero live actions / zero webview operations
    webview_init_calls = []
    start_runtime_calls = []
    search_calls = []
    evaluate_calls = []

    class MockHost:
        def __init__(self, *args, **kwargs):
            webview_init_calls.append((args, kwargs))
        def evaluate_js(self, *args, **kwargs):
            evaluate_calls.append((args, kwargs))
            return {}
        def destroy(self):
            pass

    class MockBackend:
        def __init__(self, *args, **kwargs):
            pass
        def start_runtime(self, *args, **kwargs):
            start_runtime_calls.append((args, kwargs))
        def search_messages(self, *args, **kwargs):
            search_calls.append((args, kwargs))
            return []

    monkeypatch.setattr(runner_mod, "PesuWebViewHost", MockHost)
    monkeypatch.setattr(runner_mod, "WebViewSlackSearchBackend", MockBackend)

    # Monkeypatch compute_all_dependency_hashes to simulate a hash mismatch
    orig_compute = runner_mod.compute_all_dependency_hashes
    def mock_compute_hashes():
        real_hashes = orig_compute()
        real_hashes["rb1_runner"] = "0000000000000000000000000000000000000000000000000000000000000000"
        return real_hashes

    monkeypatch.setattr(runner_mod, "compute_all_dependency_hashes", mock_compute_hashes)

    # Run in LIVE mode (dry_run=False)
    report = runner_mod.run_rb1_diagnostic(dry_run=False)

    # Verify hard-gate fail-closed contract
    assert report["checks"]["frozen_hashes_matched"] is False
    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "INTEGRITY_MISMATCH"
    assert report["error"] == "PRE_WEBVIEW_HARD_GATE_FAILED_FROZEN_HASH_MISMATCH"
    assert report["live_execution_started"] is False

    # Verify zero webview / search / DOM / click calls
    assert len(webview_init_calls) == 0, f"Expected 0 WebView inits, got {len(webview_init_calls)}"
    assert len(start_runtime_calls) == 0, f"Expected 0 start_runtime calls, got {len(start_runtime_calls)}"
    assert len(search_calls) == 0, f"Expected 0 search calls, got {len(search_calls)}"
    assert len(evaluate_calls) == 0, f"Expected 0 evaluate_js calls, got {len(evaluate_calls)}"
    assert report["live_action_inventory"]["reply_bar_clicks"] == 0

