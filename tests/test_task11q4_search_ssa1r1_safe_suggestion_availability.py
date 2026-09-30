#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PESU Agent - Phase 13.5D / Task 11 / Search Safety / SSA1-R1 Tests
Unit, AST Security, Parity, Schema Regression, and Mocked Live Integration Test Suite
"""

from __future__ import annotations

import ast
import inspect
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Optional
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from scripts.diagnose_task11q4_search_safe_suggestion_availability_ssa1r1 import (
    TARGET_QUERY,
    SUGGESTION_OBSERVATION_TIMEOUT_MS,
    POLL_INTERVAL_MS,
    MAX_SUGGESTION_OBSERVATION_POLLS,
    PROTOCOL_FREEZE_FILE,
    validate_query_setter_response,
    verify_query_setter_response_contract_parity,
    classify_ssa1r1_terminal_state,
    sanitize_report_for_persistence,
    verify_static_zero_submit_contract,
    verify_static_zero_interaction_contract,
    verify_production_candidate_classifier_parity,
    verify_query_entry_primitive_parity,
    build_ssa1r1_runtime_call_spec,
    verify_runtime_api_signature_parity,
    extract_js_function,
    normalize_js_code,
    run_ssa1r1_qualification,
    get_file_sha256,
)
from pesu_agent.runtime.pesu_webview_host import PesuWebViewHost
from pesu_agent.search.webview_slack_search import (
    JS_CHECK_AUTH_AND_READINESS,
    JS_ACTIVATE_SEARCH,
    JS_CHECK_SEARCH_INPUT_READINESS,
    JS_SET_TEXTY_QUERY,
    JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER,
    WebViewRuntimeConfig,
    WebViewSlackSearchBackend,
)


# ===========================================================================
# 1. Query Setter Response Schema & Parity Gates (SSA1-R1 Core Remediation)
# ===========================================================================

def test_validate_query_setter_response_contract_acceptance_and_rejection():
    # 1. Exact production payload must pass
    prod_payload = {"ok": True, "setter_succeeded": True}
    assert validate_query_setter_response(prod_payload) is True

    # 2. Generic 'success' must be rejected
    assert validate_query_setter_response({"success": True}) is False
    assert validate_query_setter_response({"success": True, "ok": True}) is False
    assert validate_query_setter_response({"success": True, "setter_succeeded": True}) is False

    # 3. Partial or boolean-false payloads must be rejected
    assert validate_query_setter_response({"ok": True}) is False
    assert validate_query_setter_response({"setter_succeeded": True}) is False
    assert validate_query_setter_response({"ok": False, "setter_succeeded": True}) is False
    assert validate_query_setter_response({"ok": True, "setter_succeeded": False}) is False
    assert validate_query_setter_response({"ok": False, "setter_succeeded": False}) is False

    # 4. Truthy non-booleans must be rejected
    assert validate_query_setter_response({"ok": 1, "setter_succeeded": 1}) is False
    assert validate_query_setter_response({"ok": "true", "setter_succeeded": "true"}) is False

    # 5. Non-dict and None must be rejected
    assert validate_query_setter_response(None) is False
    assert validate_query_setter_response({}) is False
    assert validate_query_setter_response("ok") is False
    assert validate_query_setter_response([prod_payload]) is False


def test_original_bug_regression_runner_response_schema_fix():
    # Exact reproduction of SSA1 consumed failure vs SSA1-R1 fix:
    prod_response = {"ok": True, "setter_succeeded": True}

    # Consumed SSA1 bug: checked res_set.get("success")
    ssa1_consumed_check = bool(isinstance(prod_response, dict) and prod_response.get("success"))
    assert ssa1_consumed_check is False, "Consumed SSA1 check incorrectly thought production payload had success"

    # SSA1-R1 fix: checks validate_query_setter_response(res_set)
    ssa1r1_fix_check = validate_query_setter_response(prod_response)
    assert ssa1r1_fix_check is True, "SSA1-R1 validator must accept exact production payload"


def test_query_setter_response_contract_parity_gate():
    ok, msg = verify_query_setter_response_contract_parity()
    assert ok is True, f"Response contract parity failed: {msg}"
    assert msg == "EXACT_QUERY_SETTER_RESPONSE_CONTRACT_PARITY_PROVEN"


def test_query_setter_response_contract_drift_fails(monkeypatch):
    import scripts.diagnose_task11q4_search_safe_suggestion_availability_ssa1r1 as ssa1r1_mod
    monkeypatch.setattr(
        ssa1r1_mod,
        "validate_query_setter_response",
        lambda res: isinstance(res, dict) and res.get("success") is True,
    )
    ok, msg = ssa1r1_mod.verify_query_setter_response_contract_parity()
    assert ok is False
    assert "Validator" in msg or "invalid payload" in msg


# ===========================================================================
# 2. Production Classifier Parity Gates
# ===========================================================================

def test_production_candidate_classifier_parity_exact():
    ok, msg = verify_production_candidate_classifier_parity()
    assert ok is True, f"Classifier parity failed: {msg}"
    assert msg == "EXACT_PARITY_PROVEN"


def test_production_classifier_drift_fails(monkeypatch):
    import scripts.diagnose_task11q4_search_safe_suggestion_availability_ssa1r1 as ssa1r1_mod
    orig_js = ssa1r1_mod.JS_OBSERVE_SEARCH_SUGGESTION_SURFACE
    drifted_js = orig_js.replace("s.suggestion_sub_type === 'query-slackbot'", "false")
    monkeypatch.setattr(ssa1r1_mod, "JS_OBSERVE_SEARCH_SUGGESTION_SURFACE", drifted_js)

    ok, msg = ssa1r1_mod.verify_production_candidate_classifier_parity()
    assert ok is False
    assert "Classifier function body mismatch" in msg or "Missing" in msg


def test_positive_safe_predicate_drift_causes_fail(monkeypatch):
    import scripts.diagnose_task11q4_search_safe_suggestion_availability_ssa1r1 as ssa1r1_mod
    orig_js = ssa1r1_mod.JS_OBSERVE_SEARCH_SUGGESTION_SURFACE
    drifted_js = orig_js.replace("s.suggestion_sub_type === 'query-echo'", "s.suggestion_sub_type === 'query-prefix'")
    monkeypatch.setattr(ssa1r1_mod, "JS_OBSERVE_SEARCH_SUGGESTION_SURFACE", drifted_js)

    ok, msg = ssa1r1_mod.verify_production_candidate_classifier_parity()
    assert ok is False


def test_unsafe_exclusion_drift_causes_fail(monkeypatch):
    import scripts.diagnose_task11q4_search_safe_suggestion_availability_ssa1r1 as ssa1r1_mod
    orig_js = ssa1r1_mod.JS_OBSERVE_SEARCH_SUGGESTION_SURFACE
    drifted_js = orig_js.replace("s.is_type_channel === true || s.type === 'channel'", "false")
    monkeypatch.setattr(ssa1r1_mod, "JS_OBSERVE_SEARCH_SUGGESTION_SURFACE", drifted_js)

    ok, msg = ssa1r1_mod.verify_production_candidate_classifier_parity()
    assert ok is False


# ===========================================================================
# 3. Query Entry Primitive Parity
# ===========================================================================

def test_query_entry_primitive_parity_exact():
    ok, msg = verify_query_entry_primitive_parity()
    assert ok is True
    assert msg == "EXACT_PRODUCTION_JS_QUERY_ENTRY_PRIMITIVES_REUSED"


def test_query_entry_primitive_drift_causes_fail(monkeypatch):
    import scripts.diagnose_task11q4_search_safe_suggestion_availability_ssa1r1 as ssa1r1_mod
    monkeypatch.setattr(ssa1r1_mod, "JS_SET_TEXTY_QUERY", "return false;")
    ok, msg = ssa1r1_mod.verify_query_entry_primitive_parity()
    assert ok is False
    assert "Primitive reference mismatch" in msg


# ===========================================================================
# 4. Runtime API Signature Parity & Shared Call Spec
# ===========================================================================

def test_runtime_api_signature_parity_and_binds():
    spec = build_ssa1r1_runtime_call_spec(timeout=45.0)
    ok, msg = verify_runtime_api_signature_parity(spec)
    assert ok is True
    assert msg == "PASS"


def test_runtime_signature_drift_causes_fail():
    spec = build_ssa1r1_runtime_call_spec(timeout=45.0)
    spec["host_kwargs"]["non_existent_arg"] = 123
    ok, msg = verify_runtime_api_signature_parity(spec)
    assert ok is False
    assert "signature bind failed" in msg


def test_shared_runtime_call_spec():
    spec = build_ssa1r1_runtime_call_spec(timeout=30.0, profile_path="C:/custom/profile")
    assert spec["host_cls"] is PesuWebViewHost
    assert spec["host_kwargs"]["user_data_folder"] == "C:/custom/profile"
    assert spec["config_cls"] is WebViewRuntimeConfig
    assert spec["backend_cls"] is WebViewSlackSearchBackend
    assert spec["start_runtime_kwargs"]["timeout"] == 30.0


# ===========================================================================
# 5. Observation Cadence & Envelope (4000ms, 50ms, <=80 polls)
# ===========================================================================

def test_observation_envelope_cadence_4000ms_50ms_80_polls():
    assert SUGGESTION_OBSERVATION_TIMEOUT_MS == 4000
    assert POLL_INTERVAL_MS == 50
    assert MAX_SUGGESTION_OBSERVATION_POLLS == 80


def test_safe_candidate_early_stop():
    polls = [
        {
            "poll_index": 1,
            "elapsed_ms": 50.0,
            "safe_search_candidate_present": True,
            "connected_candidate_count": 2,
            "safe_candidate_count": 1,
            "unsafe_candidate_count": 1,
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_NON_SEARCH_CHANNEL_NAV"],
            "suggestion_surface_present": True,
        }
    ]
    term_cls, details = classify_ssa1r1_terminal_state(polls, query_state_all_synced=True)
    assert term_cls == "SAFE_SEARCH_SUGGESTION_OBSERVED"
    assert details["first_safe_candidate_poll_index"] == 1


def test_delayed_safe_candidate_classification():
    polls = [
        {
            "poll_index": 1,
            "elapsed_ms": 50.0,
            "safe_search_candidate_present": False,
            "connected_candidate_count": 1,
            "safe_candidate_count": 0,
            "unsafe_candidate_count": 1,
            "candidate_classifications": ["UNSAFE_NON_SEARCH_CHANNEL_NAV"],
            "suggestion_surface_present": True,
        },
        {
            "poll_index": 2,
            "elapsed_ms": 100.0,
            "safe_search_candidate_present": True,
            "connected_candidate_count": 2,
            "safe_candidate_count": 1,
            "unsafe_candidate_count": 1,
            "candidate_classifications": ["UNSAFE_NON_SEARCH_CHANNEL_NAV", "SAFE_EXPLICIT_SEARCH"],
            "suggestion_surface_present": True,
        },
    ]
    term_cls, details = classify_ssa1r1_terminal_state(polls, query_state_all_synced=True)
    assert term_cls == "SAFE_SUGGESTION_APPEARED_AFTER_BOUNDED_DELAY"
    assert details["first_safe_candidate_poll_index"] == 2


def test_unsafe_only_classification():
    polls = [
        {
            "poll_index": i,
            "elapsed_ms": i * 50.0,
            "safe_search_candidate_present": False,
            "connected_candidate_count": 2,
            "safe_candidate_count": 0,
            "unsafe_candidate_count": 2,
            "candidate_classifications": ["UNSAFE_NON_SEARCH_CHANNEL_NAV", "UNSAFE_PERSON_NAV"],
            "suggestion_surface_present": True,
        }
        for i in range(1, 5)
    ]
    term_cls, details = classify_ssa1r1_terminal_state(polls, query_state_all_synced=True)
    assert term_cls == "ONLY_UNSAFE_NON_SEARCH_SUGGESTIONS_OBSERVED"
    assert details["first_safe_candidate_poll_index"] is None


def test_no_surface_observed_classification():
    polls = [
        {
            "poll_index": 1,
            "elapsed_ms": 50.0,
            "safe_search_candidate_present": False,
            "connected_candidate_count": 0,
            "safe_candidate_count": 0,
            "unsafe_candidate_count": 0,
            "candidate_classifications": [],
            "suggestion_surface_present": False,
        }
    ]
    term_cls, details = classify_ssa1r1_terminal_state(polls, query_state_all_synced=True)
    assert term_cls == "NO_SUGGESTION_SURFACE_OBSERVED"


# ===========================================================================
# 6. Corrected Fidelity Mocked Live-Path Integration (dry_run=False)
# ===========================================================================

class MockLiveWebViewHostR1:
    def __init__(self, setter_response: Optional[dict[str, Any]] = None, **kwargs):
        self.kwargs = kwargs
        self.evaluate_calls = []
        self.scenario_responses = []
        self.scenario_idx = 0
        self.closed = False
        # Exact production response schema by default
        self.setter_response = setter_response if setter_response is not None else {"ok": True, "setter_succeeded": True}

    def evaluate_js(self, script: str, timeout: float = 10.0) -> Any:
        self.evaluate_calls.append({"script": script, "timeout": timeout})
        if "isLoginScreen" in script:
            return {"ready": True, "auth_required": False, "search_button_count": 1}
        if "searchButtons[0]" in script or "dispatch_search_shortcut" in script:
            return {"activated": True, "method": "click"}
        if "searchModalCandidates" in script:
            return {"ready": True, "modal_count": 1, "input_count": 1, "strong_match_count": 1}
        if "setContents" in script and "displayQueryOps" in script:
            return self.setter_response
        if "candidate_summaries" in script:
            if self.scenario_idx < len(self.scenario_responses):
                res = self.scenario_responses[self.scenario_idx]
                self.scenario_idx += 1
                return res
            return self.scenario_responses[-1] if self.scenario_responses else {}
        return {}

    def close(self):
        self.closed = True


class MockLiveBackendR1:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False

    def start_runtime(self, timeout: float = 25.0):
        self.started = True

    def close(self):
        self.closed = True


def test_mocked_live_branch_scenario_a_immediate_safe(tmp_path):
    marker_path = tmp_path / "marker_scenario_a.json"
    output_path = tmp_path / "live_scenario_a.json"

    host = MockLiveWebViewHostR1()
    host.scenario_responses = [
        {
            "observation_ok": True,
            "query_state_all_synced": True,
            "suggestion_surface_present": True,
            "connected_candidate_count": 2,
            "safe_candidate_count": 1,
            "unsafe_candidate_count": 1,
            "safe_search_candidate_present": True,
            "candidate_classifications": ["SAFE_EXPLICIT_SEARCH", "UNSAFE_NON_SEARCH_CHANNEL_NAV"],
            "default_candidate_classification": "SAFE_EXPLICIT_SEARCH",
            "selected_candidate_classification": "SAFE_EXPLICIT_SEARCH",
            "explicit_search_candidate_present": True,
            "candidate_summaries": [
                {"index": 0, "classification": "SAFE_EXPLICIT_SEARCH", "is_safe": True, "is_unsafe": False},
                {"index": 1, "classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV", "is_safe": False, "is_unsafe": True},
            ],
        }
    ]

    report = run_ssa1r1_qualification(
        dry_run=False,
        timeout=10.0,
        marker_file_override=marker_path,
        output_file_override=output_path,
        host_factory=lambda **kw: host,
        backend_factory=lambda **kw: MockLiveBackendR1(**kw),
    )

    assert report["verdict"] == "QUALIFICATION_COMPLETED"
    assert report["terminal_classification"] == "SAFE_SEARCH_SUGGESTION_OBSERVED"
    assert report["observation_telemetry"]["actual_poll_count"] == 1
    assert report["observation_telemetry"]["first_safe_candidate_poll_index"] == 1
    assert report["checks"]["attempt_marker_written"] is True
    assert report["checks"]["query_entry_executed"] is True
    assert marker_path.exists()
    assert output_path.exists()
    assert host.closed is True

    # Audit strict zero mutations
    inv = report["live_action_inventory"]
    assert inv["search_operations"] == 1
    assert inv["query_entries"] == 1
    assert inv["actual_submit_attempts"] == 0
    assert inv["actual_submit_successes"] == 0
    assert inv["enter_key_events"] == 0
    assert inv["suggestion_clicks"] == 0
    assert inv["navigation_actions"] == 0
    assert inv["scroll_mutations"] == 0
    assert inv["provider_invocations"] == 0
    assert inv["retries"] == 0


def test_mocked_live_branch_scenario_b_delayed_safe(tmp_path):
    marker_path = tmp_path / "marker_scenario_b.json"
    output_path = tmp_path / "live_scenario_b.json"

    host = MockLiveWebViewHostR1()
    host.scenario_responses = [
        # Poll 1: only unsafe
        {
            "observation_ok": True,
            "query_state_all_synced": True,
            "suggestion_surface_present": True,
            "connected_candidate_count": 1,
            "safe_candidate_count": 0,
            "unsafe_candidate_count": 1,
            "safe_search_candidate_present": False,
            "candidate_classifications": ["UNSAFE_NON_SEARCH_CHANNEL_NAV"],
            "default_candidate_classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV",
            "selected_candidate_classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV",
            "explicit_search_candidate_present": False,
            "candidate_summaries": [
                {"index": 0, "classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV", "is_safe": False, "is_unsafe": True},
            ],
        },
        # Poll 2: safe appears
        {
            "observation_ok": True,
            "query_state_all_synced": True,
            "suggestion_surface_present": True,
            "connected_candidate_count": 2,
            "safe_candidate_count": 1,
            "unsafe_candidate_count": 1,
            "safe_search_candidate_present": True,
            "candidate_classifications": ["UNSAFE_NON_SEARCH_CHANNEL_NAV", "SAFE_EXPLICIT_SEARCH"],
            "default_candidate_classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV",
            "selected_candidate_classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV",
            "explicit_search_candidate_present": True,
            "candidate_summaries": [
                {"index": 0, "classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV", "is_safe": False, "is_unsafe": True},
                {"index": 1, "classification": "SAFE_EXPLICIT_SEARCH", "is_safe": True, "is_unsafe": False},
            ],
        },
    ]

    report = run_ssa1r1_qualification(
        dry_run=False,
        timeout=10.0,
        marker_file_override=marker_path,
        output_file_override=output_path,
        host_factory=lambda **kw: host,
        backend_factory=lambda **kw: MockLiveBackendR1(**kw),
    )

    assert report["verdict"] == "QUALIFICATION_COMPLETED"
    assert report["terminal_classification"] == "SAFE_SUGGESTION_APPEARED_AFTER_BOUNDED_DELAY"
    assert report["observation_telemetry"]["actual_poll_count"] == 2
    assert report["observation_telemetry"]["first_safe_candidate_poll_index"] == 2
    assert report["checks"]["attempt_marker_written"] is True
    assert report["checks"]["query_entry_executed"] is True
    assert host.closed is True


def test_mocked_live_branch_scenario_c_unsafe_only(tmp_path):
    marker_path = tmp_path / "marker_scenario_c.json"
    output_path = tmp_path / "live_scenario_c.json"

    host = MockLiveWebViewHostR1()
    host.scenario_responses = [
        {
            "observation_ok": True,
            "query_state_all_synced": True,
            "suggestion_surface_present": True,
            "connected_candidate_count": 2,
            "safe_candidate_count": 0,
            "unsafe_candidate_count": 2,
            "safe_search_candidate_present": False,
            "candidate_classifications": ["UNSAFE_NON_SEARCH_CHANNEL_NAV", "UNSAFE_PERSON_NAV"],
            "default_candidate_classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV",
            "selected_candidate_classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV",
            "explicit_search_candidate_present": False,
            "candidate_summaries": [
                {"index": 0, "classification": "UNSAFE_NON_SEARCH_CHANNEL_NAV", "is_safe": False, "is_unsafe": True},
                {"index": 1, "classification": "UNSAFE_PERSON_NAV", "is_safe": False, "is_unsafe": True},
            ],
        }
    ]

    report = run_ssa1r1_qualification(
        dry_run=False,
        timeout=10.0,
        marker_file_override=marker_path,
        output_file_override=output_path,
        host_factory=lambda **kw: host,
        backend_factory=lambda **kw: MockLiveBackendR1(**kw),
    )

    assert report["verdict"] == "QUALIFICATION_COMPLETED"
    assert report["terminal_classification"] == "ONLY_UNSAFE_NON_SEARCH_SUGGESTIONS_OBSERVED"
    assert report["observation_telemetry"]["first_safe_candidate_poll_index"] is None


def test_mocked_live_branch_scenario_d_no_surface(tmp_path):
    marker_path = tmp_path / "marker_scenario_d.json"
    output_path = tmp_path / "live_scenario_d.json"

    host = MockLiveWebViewHostR1()
    host.scenario_responses = [
        {
            "observation_ok": True,
            "query_state_all_synced": True,
            "suggestion_surface_present": False,
            "connected_candidate_count": 0,
            "safe_candidate_count": 0,
            "unsafe_candidate_count": 0,
            "safe_search_candidate_present": False,
            "candidate_classifications": [],
            "candidate_summaries": [],
        }
    ]

    report = run_ssa1r1_qualification(
        dry_run=False,
        timeout=10.0,
        marker_file_override=marker_path,
        output_file_override=output_path,
        host_factory=lambda **kw: host,
        backend_factory=lambda **kw: MockLiveBackendR1(**kw),
    )

    assert report["verdict"] == "QUALIFICATION_COMPLETED"
    assert report["terminal_classification"] == "NO_SUGGESTION_SURFACE_OBSERVED"


def test_mocked_live_branch_scenario_e_generic_success_rejected(tmp_path):
    marker_path = tmp_path / "marker_scenario_e.json"
    output_path = tmp_path / "live_scenario_e.json"

    # Simulate old faulty mock response shape
    host = MockLiveWebViewHostR1(setter_response={"success": True, "query_state_all_synced": True})

    report = run_ssa1r1_qualification(
        dry_run=False,
        timeout=10.0,
        marker_file_override=marker_path,
        output_file_override=output_path,
        host_factory=lambda **kw: host,
        backend_factory=lambda **kw: MockLiveBackendR1(**kw),
    )

    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "QUERY_SETTER_RESPONSE_CONTRACT_INVALID"
    assert "failed contract validation" in report["error"]
    assert report["checks"]["query_entry_executed"] is False


# ===========================================================================
# 7. AST Security & Strict Zero Mutations
# ===========================================================================

def test_zero_submit_static_ast_audit():
    ok = verify_static_zero_submit_contract()
    assert ok is True


def test_zero_click_navigation_ast_audit():
    ok = verify_static_zero_interaction_contract()
    assert ok is True


# ===========================================================================
# 8. Sensitive Data & Privacy Sanitization
# ===========================================================================

def test_sensitive_data_audit_persistence():
    payload = {
        "text": "secret slack message",
        "url": "https://company.slack.com/archives/C123/p456",
        "nested": {
            "token": ('xox' + 'b-12345-67890'),
            "email": "user@example.com",
            "safe_count": 1,
        },
    }
    sanitized = sanitize_report_for_persistence(payload)
    assert "text" not in sanitized
    assert "url" not in sanitized
    assert "token" not in sanitized["nested"]
    assert "email" not in sanitized["nested"]
    assert sanitized["nested"]["safe_count"] == 1


# ===========================================================================
# 9. Dry-Run Execution & Protocol Freeze 30 Baselines Integrity
# ===========================================================================

def test_dry_run_execution_generates_valid_artifact(tmp_path):
    dry_out = tmp_path / "dry_out.json"
    report = run_ssa1r1_qualification(dry_run=True, output_file_override=dry_out)
    assert report["verdict"] == "DRY_RUN_PASS"
    assert report["diagnostic_verdict"] == "SAFE_SEARCH_SUGGESTION_OBSERVED"
    assert dry_out.exists()


def test_protocol_freeze_integrity_and_30_baselines():
    assert PROTOCOL_FREEZE_FILE.exists()
    with open(PROTOCOL_FREEZE_FILE, "r", encoding="utf-8") as f:
        proto = json.load(f)

    baselines = proto.get("historical_baselines", {})
    assert len(baselines) == 30, f"Expected exactly 30 historical baselines, found {len(baselines)}"

    for name, base_info in baselines.items():
        rel = base_info.get("path") if isinstance(base_info, dict) else base_info
        expected_sha = base_info.get("sha256") if isinstance(base_info, dict) else base_info
        full_p = REPO_ROOT / rel
        assert full_p.exists(), f"Historical baseline file missing: {rel}"
        actual_sha = get_file_sha256(full_p)
        assert actual_sha == expected_sha, f"SHA mismatch for baseline {name} ({rel}): {actual_sha} != {expected_sha}"
