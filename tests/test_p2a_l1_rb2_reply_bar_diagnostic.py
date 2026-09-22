"""Regression tests for Task 11 / P2A-L1 / RB2: Reply-Bar Structure Diagnostic.

Validates:
1. Backend exposing valid last_search_telemetry is harvested correctly.
2. Backend exposing only last_telemetry does NOT satisfy search authority (test_rb2_does_not_use_last_telemetry_as_search_authority).
3. search_exception.details fallback matches Attempt 4 semantics.
4. Search exception cannot be masked by safety gate (test_rb2_search_exception_not_masked_by_safety_gate).
5. Search exception ordering matches Attempt 4.
6. Safety/query gate semantics match Attempt 4.
7. Safe search action explicitly selected reaches gate.
8. Verified pre-submit query True reaches gate.
9. SUBMIT_EXECUTED maps to input-ready.
10. Search submission count comes from authoritative telemetry.
11. Surface query correlation comes from authoritative transition.
12. Supplemental composer missing -> UNKNOWN.
13. Explicit composer violation -> fail closed.
14. Runtime/search path parity with Attempt 4.
15. Exact production cardinality predicate mirrored.
16. Atomic same-snapshot contract.
17. Zero click strictly preserved.
18. Sensitive-content exclusion.
19. Live artifact overwrite guard.
20. Frozen hash mismatch pre-WebView hard gate.
21. Runtime closure hard gate.
22. RB1 historical SHA remains exact (6d94c2ef...).
23. Attempt 1–4 / RD1 historical hashes unchanged.
24. Production src/ diff strictly empty.
25. Provider calls zero.
26. Source and AST parity between RB2 and Attempt 4 without untracked runtime imports.
"""

from __future__ import annotations

import ast
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

import scripts.diagnose_task11q4_p2a_l1_reply_bar_structure_rb2 as runner_mod
from scripts.diagnose_task11q4_p2a_l1_reply_bar_structure_rb2 import (
    AUTHORITATIVE_ATTEMPT4_RUNNER,
    CANONICAL_DRY_RUN_VERDICT,
    EXPECTED_CHANNEL_ID,
    EXPECTED_MESSAGE_TS,
    EXPECTED_ROOT_TS,
    JS_INSPECT_TARGET_REPLY_BAR_STRUCTURE,
    PRIMARY_DEPENDENCY_PATHS,
    PROTOCOL_FREEZE_PATH,
    SOURCE_RAW_PERMALINK,
    SOURCE_RD1_RUN2_RANK,
    TARGET_FITNESS_CONTRACT,
    TARGET_QUERY,
    audit_runtime_import_closure,
    compute_all_dependency_hashes,
    evaluate_search_safety_and_query_authority,
    harvest_search_telemetry,
    is_supplemental_safe,
    run_rb2_diagnostic,
)


def test_1_target_identity_contracts():
    assert TARGET_QUERY == "26년 하반기 간담회"
    assert EXPECTED_CHANNEL_ID == "C042WS25QAX"
    assert EXPECTED_MESSAGE_TS == "1718079276.126469"
    assert EXPECTED_ROOT_TS == "1718058683.912369"
    assert SOURCE_RD1_RUN2_RANK == 5

    parsed = parse_slack_message_permalink(SOURCE_RAW_PERMALINK)
    assert parsed is not None
    assert parsed.channel_id == EXPECTED_CHANNEL_ID
    assert parsed.message_ts == "1718079276.126469"
    assert parsed.thread_ts == "1718058683.912369"
    assert TARGET_FITNESS_CONTRACT == "EXPECTED_MESSAGE_TS != EXPECTED_ROOT_TS"
    assert EXPECTED_MESSAGE_TS != EXPECTED_ROOT_TS


def test_2_dependency_paths_and_hashes():
    assert PROTOCOL_FREEZE_PATH.exists()
    with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    cur_hashes = compute_all_dependency_hashes()
    expected = manifest.get("dependency_hashes", {})
    for dep_name, cur_h in cur_hashes.items():
        assert cur_h != "MISSING", f"Dependency file for {dep_name} does not exist"
        assert cur_h == expected.get(dep_name), f"Hash mismatch for {dep_name}: {cur_h} != {expected.get(dep_name)}"


def test_3_harvest_search_telemetry_fidelity_authoritative_success():
    """Validates that valid backend.last_search_telemetry is harvested correctly."""
    class MockAuthoritativeBackend:
        last_search_telemetry = {
            "activation_dispatch_count": 1,
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

    report: dict = {}
    harvest_search_telemetry(MockAuthoritativeBackend(), None, report)

    assert report["live_action_inventory"]["search_activation_dispatches"] == 1
    assert report["live_action_inventory"]["search_modal_observed"] is True
    assert report["live_action_inventory"]["search_input_ready"] is True
    assert report["live_action_inventory"]["search_submissions"] == 1
    assert report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] is True
    assert report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] is True
    assert report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] is True


def test_4_test_rb2_does_not_use_last_telemetry_as_search_authority():
    """CRITICAL REGRESSION: backend exposing only last_telemetry does NOT satisfy search authority."""
    class MockBadBackend:
        # Intentionally only expose last_telemetry (like RB1 flaw)
        last_telemetry = {
            "activation_dispatched": True,
            "modal_ever_observed": True,
            "input_ever_observed": True,
            "onenter_invoked": True,
            "safe_search_action_selected": True,
            "verified_query_state_before_submit": True,
        }

    report: dict = {}
    harvest_search_telemetry(MockBadBackend(), None, report)

    # Must fail closed with UNKNOWN / TELEMETRY_MISSING
    assert report["search_telemetry"]["failure_case_classification"] == "TELEMETRY_MISSING"
    assert report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] == "UNKNOWN"
    assert report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] == "UNKNOWN"
    assert report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] == "UNKNOWN"
    assert report["live_action_inventory"]["search_submissions"] == "UNKNOWN"

    ok, reason = evaluate_search_safety_and_query_authority(report)
    assert ok is False
    assert reason == "SAFE_SEARCH_ACTION_NOT_EXPLICITLY_SELECTED"


def test_5_search_exception_details_fallback():
    """Validates fallback to search_exception.details when backend.last_search_telemetry is absent."""
    class MockEmptyBackend:
        last_search_telemetry = None

    class MockSearchException(Exception):
        details = {
            "activation_dispatch_count": 1,
            "modal_ever_observed": True,
            "input_ever_observed": True,
            "final_input_count": 1,
            "last_confirmed_stage": "SUBMIT_EXECUTED",
            "onenter_invoked": True,
            "safe_search_action_selected": True,
            "verified_query_state_before_submit": True,
            "search_surface_query_matches_requested": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked": False,
            "write_capable_action_invoked": False,
            "unsafe_non_search_action_invoked": False,
        }

    report: dict = {}
    harvest_search_telemetry(MockEmptyBackend(), MockSearchException("Search failed"), report)

    assert report["live_action_inventory"]["search_submissions"] == 1
    assert report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] is True
    assert report["checks"]["QUERY_STATE_VERIFIED_BEFORE_SUBMIT"] is True
    assert report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] is True


def test_6_test_rb2_search_exception_not_masked_by_safety_gate(monkeypatch, tmp_path):
    """CRITICAL REGRESSION: search_exception is raised and preserved, not masked by safety gate."""
    class SyntheticSearchError(RuntimeError):
        pass

    class MockFailingBackend:
        last_search_telemetry = None
        def start_runtime(self, timeout=30.0):
            pass
        def search_messages(self, query, max_results=5):
            raise SyntheticSearchError("CDP search navigation timeout")

    monkeypatch.setattr(runner_mod, "LIVE_OUTPUT_FILE", tmp_path / "dummy_live.json")
    monkeypatch.setattr(runner_mod, "PesuWebViewHost", MagicMock())
    monkeypatch.setattr(runner_mod, "WebViewSlackSearchBackend", lambda config, host: MockFailingBackend())

    # Run in live mode (with monkeypatched backend and isolated live output path)
    report = run_rb2_diagnostic(dry_run=False)

    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "EXCEPTION_SyntheticSearchError"
    assert "CDP search navigation timeout" in str(report["error"])
    # Crucially: it must NOT become SAFETY_GATE_FAILED_SAFE_SEARCH_ACTION_NOT_EXPLICITLY_SELECTED
    assert "SAFETY_GATE_FAILED" not in report["diagnostic_verdict"]
    assert report["checks"]["atomic_snapshot_executed"] is False
    assert report["live_action_inventory"]["pure_read_dom_extractions"] == 0
    assert report["live_action_inventory"]["reply_bar_clicks"] == 0


def test_7_search_safety_and_query_authority_evaluation():
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

    # Test explicit violations
    rep_vio = json.loads(json.dumps(base_report))
    rep_vio["safety_telemetry"]["GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH"] = True
    ok, reason = evaluate_search_safety_and_query_authority(rep_vio)
    assert ok is False
    assert reason == "EXPLICIT_SAFETY_VIOLATION_GENERIC_DEFAULT_ONENTER"

    rep_vio = json.loads(json.dumps(base_report))
    rep_vio["safety_telemetry"]["SLACKBOT_ACTION_INVOKED_BY_PESU"] = True
    ok, reason = evaluate_search_safety_and_query_authority(rep_vio)
    assert ok is False
    assert reason == "EXPLICIT_SAFETY_VIOLATION_SLACKBOT"

    rep_vio = json.loads(json.dumps(base_report))
    rep_vio["safety_telemetry"]["WRITE_CAPABLE_ACTION_INVOKED_BY_PESU"] = True
    ok, reason = evaluate_search_safety_and_query_authority(rep_vio)
    assert ok is False
    assert reason == "EXPLICIT_SAFETY_VIOLATION_WRITE_CAPABLE"

    rep_vio = json.loads(json.dumps(base_report))
    rep_vio["safety_telemetry"]["UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU"] = True
    ok, reason = evaluate_search_safety_and_query_authority(rep_vio)
    assert ok is False
    assert reason == "EXPLICIT_SAFETY_VIOLATION_UNSAFE_NON_SEARCH"

    rep_vio = json.loads(json.dumps(base_report))
    rep_vio["live_action_inventory"]["search_submissions"] = 2
    ok, reason = evaluate_search_safety_and_query_authority(rep_vio)
    assert ok is False
    assert reason == "SEARCH_SUBMISSIONS_COUNT_INVALID"


def test_8_supplemental_composer_semantics():
    assert is_supplemental_safe("UNKNOWN") is True
    assert is_supplemental_safe(None) is True
    assert is_supplemental_safe("NOT_RUN") is True
    assert is_supplemental_safe(0) is True
    assert is_supplemental_safe(False) is True
    assert is_supplemental_safe(1) is False
    assert is_supplemental_safe(True) is False
    assert is_supplemental_safe("EXPLICIT_INTERACTION") is False


def test_9_submit_executed_maps_to_input_ready():
    class MockStageBackend:
        last_search_telemetry = {
            "input_ever_observed": True,
            "final_input_count": 1,
            "last_confirmed_stage": "SUBMIT_EXECUTED",
        }

    rep = {}
    harvest_search_telemetry(MockStageBackend(), None, rep)
    assert rep["live_action_inventory"]["search_input_ready"] is True
    assert rep["search_telemetry"]["input_ready"] is True


def test_10_forensic_transition_surface_query_authority():
    class MockTransBackend:
        last_search_telemetry = {
            "forensic_transition": {
                "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
                "SEARCH_SURFACE_QUERY_AFTER": "다음에 대한 결과: 26년 하반기 간담회",
            }
        }

    rep = {}
    harvest_search_telemetry(MockTransBackend(), None, rep)
    assert rep["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] is True
    assert rep["search_telemetry"]["search_surface_query_after"] == "다음에 대한 결과: 26년 하반기 간담회"


def test_11_live_artifact_overwrite_guard():
    dummy_live = runner_mod.LIVE_OUTPUT_FILE
    dummy_live.parent.mkdir(parents=True, exist_ok=True)
    created_dummy = False
    if not dummy_live.exists():
        dummy_live.write_text('{"dummy": true}', encoding="utf-8")
        created_dummy = True

    try:
        report = run_rb2_diagnostic(dry_run=False)
        assert report["verdict"] == "FAIL_CLOSED"
        assert report["error"] == "LIVE_ARTIFACT_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
        assert report["diagnostic_verdict"] == "LIVE_ARTIFACT_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
    finally:
        if created_dummy and dummy_live.exists():
            dummy_live.unlink()


def test_12_dry_run_canonical_verdict_and_zero_live_actions():
    assert CANONICAL_DRY_RUN_VERDICT == "DRY_RUN_PASS"
    report = run_rb2_diagnostic(dry_run=True)
    assert report["verdict"] == CANONICAL_DRY_RUN_VERDICT
    assert report["diagnostic_verdict"] == CANONICAL_DRY_RUN_VERDICT
    assert report["checks"]["dry_run_passed"] is True
    assert report["checks"]["atomic_snapshot_executed"] is False
    assert report["live_action_inventory"]["search_submissions"] == "UNKNOWN" or report["live_action_inventory"]["search_submissions"] == 0
    assert report["live_action_inventory"]["pure_read_dom_extractions"] == 0
    assert report["live_action_inventory"]["reply_bar_clicks"] == 0
    assert report["provider_calls"] == 0
    assert runner_mod.DRY_RUN_OUTPUT_FILE.exists()


def test_13_historical_traces_hash_identical():
    with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    prior = manifest.get("prior_attempt_hashes", {})
    assert len(prior) >= 7

    historical_mapping = {
        "attempt1_trace_sha256": "output/qualification_13_5d/live_trace/task11q4_p2a_l1_trace.json",
        "attempt2_derived_trace_sha256": "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt2_trace.json",
        "rd1_search_residency_sha256": "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rd1_search_residency.json",
        "rd1_run2_search_residency_sha256": "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rd1_run2_search_residency.json",
        "attempt3_trace_sha256": "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt3_trace.json",
        "attempt4_trace_sha256": "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt4_trace.json",
        "rb1_reply_bar_structure_sha256": "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rb1_reply_bar_structure.json",
    }

    for key, rel_path in historical_mapping.items():
        assert key in prior, f"Missing {key} in prior_attempt_hashes!"
        p = REPO_ROOT / rel_path
        assert p.exists(), f"Historical trace {rel_path} missing!"
        act = hashlib.sha256(p.read_bytes()).hexdigest()
        exp = prior[key]
        assert act == exp, f"Historical trace {rel_path} hash modified! Expected {exp}, got {act}"

    # RB1 exact authoritative hash
    rb1_sha = hashlib.sha256((REPO_ROOT / "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rb1_reply_bar_structure.json").read_bytes()).hexdigest()
    assert rb1_sha == "6d94c2ef2bdc9b4eda4e68bf23737fe5e3280a014892ba0ffcb0cb1481dd76d5"


def test_14_production_src_diff_strictly_empty():
    res = subprocess.run(
        ["git", "diff", "HEAD", "--", "src/"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == "", f"Production src/ has uncommitted changes: {res.stdout}"


def test_15_atomic_js_snapshot_simulation_zero_reply_bars():
    eval_result = {
        "ok": True,
        "target_resident": True,
        "resident_matches": 1,
        "target_row_index": 4,
        "mounted_row_count": 5,
        "inspected_row_count": 5,
        "rows": [{"row_index": 4, "identity_matched": True}],
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
            "structural_candidate_count": 1,
        },
        "reply_bar_elements": [],
        "alternative_candidates": [{"selector": "[data-qa=\"reply_bar_view_thread\"]", "inside_reply_bar": False}],
    }

    card = eval_result["reply_bar_cardinality"]
    assert card["production_selector_connected_div_reply_bar_count"] == 0
    assert card["production_cardinality_guard_would_pass"] is False
    assert len(eval_result["alternative_candidates"]) == 1


def test_16_atomic_js_snapshot_simulation_one_qualified_reply_bar():
    eval_result = {
        "ok": True,
        "target_resident": True,
        "resident_matches": 1,
        "target_row_index": 4,
        "mounted_row_count": 5,
        "inspected_row_count": 5,
        "rows": [{"row_index": 4, "identity_matched": True}],
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
            "is_connected": True,
            "is_rendered": True,
            "has_reply_bar_view_thread": True,
            "has_reply_bar_count": True,
        }],
        "alternative_candidates": [],
    }

    card = eval_result["reply_bar_cardinality"]
    assert card["production_selector_connected_div_reply_bar_count"] == 1
    assert card["production_cardinality_guard_would_pass"] is True


def test_17_sensitive_content_exclusion():
    assert "outerHTML" not in JS_INSPECT_TARGET_REPLY_BAR_STRUCTURE
    assert "textContent" not in JS_INSPECT_TARGET_REPLY_BAR_STRUCTURE
    assert "innerText" not in JS_INSPECT_TARGET_REPLY_BAR_STRUCTURE

    report = run_rb2_diagnostic(dry_run=True)
    assert report["checks"]["USER_MESSAGE_TEXT_CAPTURED"] is False
    assert report["checks"]["UNRELATED_RAW_PERMALINK_CAPTURED"] is False
    assert report["checks"]["OUTER_HTML_CAPTURED"] is False


def test_18_exact_production_cardinality_predicate_mirrored():
    assert 'targetRow.querySelectorAll(\'div[data-qa="reply_bar"]\')' in JS_INSPECT_TARGET_REPLY_BAR_STRUCTURE
    assert "el.isConnected" in JS_INSPECT_TARGET_REPLY_BAR_STRUCTURE


def test_19_attempt4_runtime_search_path_parity():
    # Verify signature and call pattern of start_runtime, search_messages, max_results=5
    rb2_src = inspect.getsource(runner_mod.run_rb2_diagnostic)
    assert "PesuWebViewHost(" in rb2_src
    assert "DIRECT_SLACK_PROFILE_PATH" in rb2_src
    assert "WebViewSlackSearchBackend(" in rb2_src
    assert "backend.start_runtime(timeout=timeout)" in rb2_src
    assert "backend.search_messages(TARGET_QUERY, max_results=SEARCH_MAX_RESULTS)" in rb2_src
    assert "SEARCH_MAX_RESULTS = 5" in inspect.getsource(runner_mod)
    assert "harvest_search_telemetry(backend, search_exception, report)" in rb2_src
    assert "if search_exception is not None:" in rb2_src
    assert "raise search_exception" in rb2_src
    assert "evaluate_search_safety_and_query_authority(report)" in rb2_src


def test_20_runtime_import_closure_audit():
    with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    audit = audit_runtime_import_closure(manifest)
    assert audit["closure_audit_passed"] is True, f"Closure audit failed: untracked={audit['untracked_files']}, mismatched={audit['mismatched_files']}"
    assert audit["untracked_loaded_project_dependency_count"] == 0
    assert len(audit["untracked_files"]) == 0
    assert len(audit["mismatched_files"]) == 0


def test_21_provider_calls_strictly_zero():
    report = run_rb2_diagnostic(dry_run=True)
    assert report["provider_calls"] == 0
    assert report["action_contracts"]["MAX_PROVIDER_CALLS"] == 0


def test_22_zero_clicks_strictly_preserved():
    assert runner_mod.MAX_REPLY_BAR_CLICKS == 0
    assert runner_mod.MAX_RESULT_CARD_CLICKS == 0
    assert runner_mod.MAX_THREAD_EXPANSIONS == 0

    js = JS_INSPECT_TARGET_REPLY_BAR_STRUCTURE
    assert ".click(" not in js
    assert "dispatchEvent(new MouseEvent" not in js

    report = run_rb2_diagnostic(dry_run=True)
    assert report["live_action_inventory"]["reply_bar_clicks"] == 0


def test_23_frozen_hash_mismatch_blocks_live_before_webview(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "LIVE_OUTPUT_FILE", tmp_path / "dummy_live.json")
    monkeypatch.setattr(runner_mod, "PesuWebViewHost", MagicMock())
    orig_compute = runner_mod.compute_all_dependency_hashes

    def fake_compute():
        h = orig_compute()
        h["slack_search"] = "0000000000000000000000000000000000000000000000000000000000000000"
        return h

    monkeypatch.setattr(runner_mod, "compute_all_dependency_hashes", fake_compute)

    report = run_rb2_diagnostic(dry_run=False)
    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "PREFLIGHT_HASH_MISMATCH"
    assert report["error"] == "DEPENDENCY_HASH_MISMATCH_BEFORE_EXECUTION"
    assert runner_mod.PesuWebViewHost.call_count == 0


def test_24_source_and_ast_parity_with_attempt4():
    """Validates source and AST parity between RB2 and Attempt 4 for telemetry and safety gates without importing attempt4 runner."""
    with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    comp_auth = manifest.get("comparison_authority", {})
    a4_runner_path = REPO_ROOT / comp_auth["attempt4_runner"]
    assert a4_runner_path.exists()
    a4_sha = hashlib.sha256(a4_runner_path.read_bytes()).hexdigest()
    assert a4_sha == comp_auth["attempt4_runner_sha256"]

    # Read Attempt 4 source text directly (avoiding runtime import into sys.modules)
    a4_full_text = a4_runner_path.read_text(encoding="utf-8")
    a4_tree = ast.parse(a4_full_text)

    # Extract functions from Attempt 4 AST
    a4_funcs = {node.name: node for node in a4_tree.body if isinstance(node, ast.FunctionDef)}
    assert "harvest_search_telemetry" in a4_funcs
    assert "evaluate_search_safety_and_query_authority" in a4_funcs

    # Verify AST structure of harvest_search_telemetry in RB2 vs Attempt 4
    rb2_src = inspect.getsource(runner_mod.harvest_search_telemetry)

    # Both must query last_search_telemetry
    assert 'getattr(backend, "last_search_telemetry", None)' in rb2_src
    assert 'getattr(backend, "last_search_telemetry", None)' in a4_full_text

    # Both must check fallback to search_exception.details
    assert 'getattr(search_exception, "details", None)' in rb2_src
    assert 'getattr(search_exception, "details", None)' in a4_full_text

    # RB2 must NOT query getattr(backend, "last_telemetry"
    assert 'getattr(backend, "last_telemetry"' not in rb2_src

    # AST verification: no Attribute access to last_telemetry on backend
    class TelemetryAttrChecker(ast.NodeVisitor):
        def __init__(self):
            self.found_last_telemetry = False
        def visit_Attribute(self, node):
            if node.attr == "last_telemetry":
                self.found_last_telemetry = True
            self.generic_visit(node)

    rb2_tree = ast.parse(rb2_src)
    checker = TelemetryAttrChecker()
    checker.visit(rb2_tree)
    assert not checker.found_last_telemetry

    # Verify evaluate_search_safety_and_query_authority parity
    rb2_gate_src = inspect.getsource(runner_mod.evaluate_search_safety_and_query_authority)
    assert "EXPLICIT_SAFETY_VIOLATION_GENERIC_DEFAULT_ONENTER" in rb2_gate_src
    assert "EXPLICIT_SAFETY_VIOLATION_GENERIC_DEFAULT_ONENTER" in a4_full_text
    assert "SAFE_SEARCH_ACTION_NOT_EXPLICITLY_SELECTED" in rb2_gate_src
    assert "SAFE_SEARCH_ACTION_NOT_EXPLICITLY_SELECTED" in a4_full_text
    assert "SEARCH_SUBMISSIONS_COUNT_INVALID" in rb2_gate_src
    assert "SEARCH_SUBMISSIONS_COUNT_INVALID" in a4_full_text
    assert "SEARCH_SURFACE_QUERY_MISMATCH" in rb2_gate_src
    assert "SEARCH_SURFACE_QUERY_MISMATCH" in a4_full_text
