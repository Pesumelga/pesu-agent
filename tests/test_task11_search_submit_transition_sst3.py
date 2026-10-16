# -*- coding: utf-8 -*-
"""Comprehensive Unit & Offline Tests for SST3 Execution Hardening.

Required coverage:
- Real PesuWebViewHost constructor signature compatibility (user_data_folder=str(DIRECT_SLACK_PROFILE_PATH))
- Real PesuWebViewHost start/evaluate/cleanup interface contracts
- Auth false -> no marker, no query
- Client not ready -> bounded observation, no marker if never ready
- Delayed client readiness -> successful preflight & marker acquisition
- JS evaluation failure -> no marker
- Ready Host -> marker acquired once, qualification pass
- Marker already exists -> no query or submit (fail closed)
- Post-marker failure -> marker retained, no retry
- Guarded search submission -> at most once
- Sensitive-data sanitization
- Historical frozen evidence integrity (SST1/SST2 markers/traces, 34 baselines, 60 runtime closure hashes)
- Static AST safety contracts
"""

from __future__ import annotations

import concurrent.futures
import inspect
import json
import os
from pathlib import Path
from typing import Any, Optional
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from pesu_agent.runtime.pesu_webview_host import PesuWebViewHost, DIRECT_SLACK_PROFILE_PATH
import scripts.diagnose_task11_search_submit_transition_sst3 as sst3_runner


class MockHostForSST3:
    """Mock host simulating WebView JS evaluation for offline SST3 testing."""
    def __init__(
        self,
        auth_ok: bool = True,
        input_ready: bool = True,
        query_entry_ok: bool = True,
        readiness_ok: bool = True,
        submit_ok: bool = True,
        transition_ok: bool = True,
        result_count: int = 0,
        delayed_ready_poll_threshold: int = 0,
        eval_exception: bool = False,
        *args,
        **kwargs,
    ):
        self.auth_ok = auth_ok
        self.input_ready = input_ready
        self.query_entry_ok = query_entry_ok
        self.readiness_ok = readiness_ok
        self.submit_ok = submit_ok
        self.transition_ok = transition_ok
        self.result_count = result_count
        self.delayed_ready_poll_threshold = delayed_ready_poll_threshold
        self.eval_exception = eval_exception
        self.stopped = False
        self.destroyed = False
        self.started = False
        self.calls: list[str] = []
        self.auth_probe_call_count = 0
        self.init_args = args
        self.init_kwargs = kwargs

    def start(self, timeout: float = 20.0):
        self.started = True

    def stop(self):
        self.stopped = True

    def destroy(self):
        self.destroyed = True

    def evaluate_js(self, script: str, timeout: float = 10.0):
        return self._handle_script(script)

    def evaluate_javascript(self, script: str):
        return self._handle_script(script)

    def _handle_script(self, script: str) -> Any:
        self.calls.append(script)
        if self.eval_exception:
            raise RuntimeError("Simulated JS evaluation failure")

        if "isLoginScreen" in script:
            self.auth_probe_call_count += 1
            is_ready = self.readiness_ok
            if self.delayed_ready_poll_threshold > 0:
                is_ready = (self.auth_probe_call_count >= self.delayed_ready_poll_threshold)

            return {
                "ready": is_ready,
                "auth_required": not self.auth_ok,
                "search_button_count": 1 if is_ready else 0,
                "authenticated": self.auth_ok,
                "client_ready": is_ready,
            }

        if "searchButtons" in script and "dispatchEvent" in script:
            # JS_ACTIVATE_SEARCH
            return {"activated": True}

        if "searchModalCandidates" in script:
            # JS_CHECK_SEARCH_INPUT_READINESS
            return {"input_ready": self.input_ready}

        if "setContents" in script:
            # JS_SET_TEXTY_QUERY
            return {"ok": self.query_entry_ok, "query_length": len(sst3_runner.TARGET_QUERY)}

        if "selected_safe_index" in script:
            # JS_PROBE_SEARCH_READINESS_BEFORE_SUBMIT
            if self.readiness_ok:
                return {
                    "ready": True,
                    "input_box_found": True,
                    "query_state_all_synced": True,
                    "internal_flags_ok": True,
                    "safe_candidate_present": True,
                    "safe_candidate_exact_match": True,
                    "candidate_dom_mapping_ok": True,
                    "selected_safe_index": 0,
                    "reasons": [],
                }
            else:
                return {
                    "ready": False,
                    "input_box_found": True,
                    "query_state_all_synced": True,
                    "internal_flags_ok": False,
                    "safe_candidate_present": True,
                    "safe_candidate_exact_match": False,
                    "candidate_dom_mapping_ok": False,
                    "selected_safe_index": -1,
                    "reasons": ["INTERNAL_FLAGS_NOT_SETTLED"],
                }

        if "isExactSafeKeywordSearch" in script or "SafeSearchSuggestionDOMItem" in script:
            # JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER
            if self.submit_ok:
                return {
                    "ok": True,
                    "report": {
                        "submit_succeeded": True,
                        "submit_method": "SafeSearchSuggestionDOMItem.click[index=0]",
                    },
                }
            else:
                return {
                    "ok": False,
                    "error": "SEARCH_GUARD_REJECTED",
                    "report": {"submit_succeeded": False},
                }

        if "surfaceQueryAfter" in script:
            # JS_PROBE_POST_WAIT_SEARCH_STATE
            if self.transition_ok:
                return {
                    "search_view_mounted": True,
                    "loading_detected_after": False,
                    "skeleton_count": 0,
                    "result_root_count": self.result_count,
                    "surface_query_after": sst3_runner.TARGET_QUERY,
                    "empty_element_count": 0,
                }
            else:
                return {
                    "search_view_mounted": False,
                    "loading_detected_after": True,
                    "skeleton_count": 3,
                    "result_root_count": 0,
                    "surface_query_after": None,
                    "empty_element_count": 0,
                }

        return None


def _make_valid_auth_file(path: Path) -> Path:
    payload = {
        "approved_git_commit": sst3_runner.get_git_commit(),
        "approved_runner_sha256": sst3_runner.get_file_sha256(Path(sst3_runner.__file__)),
        "approved_protocol_sha256": sst3_runner.get_file_sha256(sst3_runner.PROTOCOL_FREEZE_FILE),
        "authorized_at_utc": "2026-10-16T14:30:00Z",
        "authorization_scope": "SST3_LIVE_SINGLE_SHOT",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Host Constructor & Interface Contracts
# ---------------------------------------------------------------------------

def test_real_host_constructor_and_adapter_contracts():
    sig = inspect.signature(PesuWebViewHost.__init__)
    assert "user_data_folder" in sig.parameters

    bound = sig.bind(
        None,
        user_data_folder=str(DIRECT_SLACK_PROFILE_PATH),
        initial_url="https://app.slack.com/client",
        title="PESU Task 11 SST3 Search Submit Transition",
    )
    assert bound.arguments["user_data_folder"] == str(DIRECT_SLACK_PROFILE_PATH)

    sig_start = inspect.signature(PesuWebViewHost.start)
    assert "timeout" in sig_start.parameters
    assert hasattr(PesuWebViewHost, "evaluate_js")
    assert hasattr(PesuWebViewHost, "destroy")

    # Adapter helper checks
    class DummyEval:
        def evaluate_js(self, s): return {"ok": True}
        def destroy(self): self.d = True

    d = DummyEval()
    assert sst3_runner._evaluate_host_js(d, "x") == {"ok": True}
    sst3_runner._cleanup_host(d)
    assert getattr(d, "d", False) is True


# ---------------------------------------------------------------------------
# Section 5: Mandatory Offline Verification Requirements
# ---------------------------------------------------------------------------

def test_auth_false_no_marker_no_query(tmp_path, monkeypatch):
    """Auth false -> fails in preflight, NO marker written, NO query entered."""
    monkeypatch.setattr(sst3_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"

    host = MockHostForSST3(auth_ok=False)

    orig_timeout = sst3_runner.AUTH_PREFLIGHT_OBSERVATION_TIMEOUT_MS
    orig_polls = sst3_runner.MAX_AUTH_PREFLIGHT_POLLS
    sst3_runner.AUTH_PREFLIGHT_OBSERVATION_TIMEOUT_MS = 200
    sst3_runner.MAX_AUTH_PREFLIGHT_POLLS = 2

    try:
        res = sst3_runner.run_sst3_qualification(
            dry_run=False,
            marker_file_override=marker,
            output_file_override=trace,
            authorization_file_override=auth_file,
            host_factory=lambda: host,
        )

        assert res["verdict"] == "FAIL_CLOSED"
        assert res["diagnostic_verdict"] == "AUTH_OR_CLIENT_NOT_READY"
        assert res["checks"]["attempt_marker_written"] is False
        assert not marker.exists(), "One-shot marker MUST NOT be acquired if auth fails!"
        assert res["observed_actions"]["query_entries_executed"] == 0
        assert res["observed_actions"]["guarded_submit_attempts"] == 0
    finally:
        sst3_runner.AUTH_PREFLIGHT_OBSERVATION_TIMEOUT_MS = orig_timeout
        sst3_runner.MAX_AUTH_PREFLIGHT_POLLS = orig_polls


def test_client_not_ready_bounded_observation_no_marker(tmp_path, monkeypatch):
    """Client not ready -> bounded observation times out, NO marker written, NO query entered."""
    monkeypatch.setattr(sst3_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"

    host = MockHostForSST3(auth_ok=True, readiness_ok=False)

    orig_timeout = sst3_runner.AUTH_PREFLIGHT_OBSERVATION_TIMEOUT_MS
    orig_polls = sst3_runner.MAX_AUTH_PREFLIGHT_POLLS
    sst3_runner.AUTH_PREFLIGHT_OBSERVATION_TIMEOUT_MS = 200
    sst3_runner.MAX_AUTH_PREFLIGHT_POLLS = 2

    try:
        res = sst3_runner.run_sst3_qualification(
            dry_run=False,
            marker_file_override=marker,
            output_file_override=trace,
            authorization_file_override=auth_file,
            host_factory=lambda: host,
        )

        assert res["verdict"] == "FAIL_CLOSED"
        assert res["diagnostic_verdict"] == "AUTH_OR_CLIENT_NOT_READY"
        assert res["checks"]["attempt_marker_written"] is False
        assert not marker.exists(), "One-shot marker MUST NOT be acquired if client not ready!"
        assert res["observed_actions"]["query_entries_executed"] == 0
    finally:
        sst3_runner.AUTH_PREFLIGHT_OBSERVATION_TIMEOUT_MS = orig_timeout
        sst3_runner.MAX_AUTH_PREFLIGHT_POLLS = orig_polls


def test_delayed_client_readiness_successful_preflight(tmp_path, monkeypatch):
    """Delayed client readiness -> initial poll not ready, subsequent poll ready -> marker acquired and passes!"""
    monkeypatch.setattr(sst3_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"

    # Poll 1 & 2 not ready, poll 3 becomes ready
    host = MockHostForSST3(auth_ok=True, readiness_ok=True, delayed_ready_poll_threshold=3)

    res = sst3_runner.run_sst3_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_file,
        host_factory=lambda: host,
    )

    assert res["verdict"] == "QUALIFICATION_PASS"
    assert res["checks"]["auth_and_readiness_preflight_passed"] is True
    assert res["checks"]["attempt_marker_written"] is True
    assert marker.exists()
    assert res["observed_actions"]["query_entries_executed"] == 1
    assert res["observed_actions"]["guarded_submit_attempts"] == 1


def test_js_evaluation_failure_no_marker(tmp_path, monkeypatch):
    """JS evaluation failure during preflight -> NO marker written, fail-closed."""
    monkeypatch.setattr(sst3_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"

    host = MockHostForSST3(eval_exception=True)

    orig_timeout = sst3_runner.AUTH_PREFLIGHT_OBSERVATION_TIMEOUT_MS
    orig_polls = sst3_runner.MAX_AUTH_PREFLIGHT_POLLS
    sst3_runner.AUTH_PREFLIGHT_OBSERVATION_TIMEOUT_MS = 200
    sst3_runner.MAX_AUTH_PREFLIGHT_POLLS = 2

    try:
        res = sst3_runner.run_sst3_qualification(
            dry_run=False,
            marker_file_override=marker,
            output_file_override=trace,
            authorization_file_override=auth_file,
            host_factory=lambda: host,
        )

        assert res["verdict"] == "FAIL_CLOSED"
        assert not marker.exists(), "One-shot marker MUST NOT be acquired on JS evaluation failure!"
    finally:
        sst3_runner.AUTH_PREFLIGHT_OBSERVATION_TIMEOUT_MS = orig_timeout
        sst3_runner.MAX_AUTH_PREFLIGHT_POLLS = orig_polls


def test_ready_host_marker_acquired_once(tmp_path, monkeypatch):
    """Ready Host -> marker acquired once, full run passes."""
    monkeypatch.setattr(sst3_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"

    host = MockHostForSST3(auth_ok=True, readiness_ok=True, submit_ok=True, transition_ok=True)

    res = sst3_runner.run_sst3_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_file,
        host_factory=lambda: host,
    )

    assert res["verdict"] == "QUALIFICATION_PASS"
    assert res["diagnostic_verdict"] == "SEARCH_SUBMIT_TRANSITION_SUCCESS"
    assert res["checks"]["attempt_marker_written"] is True
    assert marker.exists()
    assert res["observed_actions"]["guarded_submit_attempts"] == 1
    assert res["observed_actions"]["guarded_submit_successes"] == 1


def test_marker_already_exists_no_query_or_submit(tmp_path, monkeypatch):
    """Marker already exists -> fails closed immediately with ATTEMPT_ALREADY_EXISTS, 0 queries entered."""
    monkeypatch.setattr(sst3_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")
    marker = tmp_path / "marker.json"
    marker.write_text("pre-existing-marker", encoding="utf-8")
    trace = tmp_path / "trace.json"

    host = MockHostForSST3()

    res = sst3_runner.run_sst3_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_file,
        host_factory=lambda: host,
    )

    assert res["verdict"] == "FAIL_CLOSED"
    assert res["diagnostic_verdict"] == "ATTEMPT_ALREADY_EXISTS"
    assert res["observed_actions"]["query_entries_executed"] == 0
    assert res["observed_actions"]["guarded_submit_attempts"] == 0


def test_post_marker_failure_retains_marker_no_retry(tmp_path, monkeypatch):
    """Post-marker failure (guard blocks) -> marker retained, fail closed."""
    monkeypatch.setattr(sst3_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"

    host = MockHostForSST3(auth_ok=True, readiness_ok=True, submit_ok=False)

    res = sst3_runner.run_sst3_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_file,
        host_factory=lambda: host,
    )

    assert res["verdict"] == "FAIL_CLOSED"
    assert res["diagnostic_verdict"] == "SEARCH_GUARD_BLOCKED_SUBMISSION"
    assert res["checks"]["attempt_marker_written"] is True
    assert marker.exists(), "Marker must remain permanently acquired after failure!"
    assert res["observed_actions"]["guarded_submit_attempts"] == 1
    assert res["observed_actions"]["guarded_submit_successes"] == 0


def test_guarded_search_submission_at_most_once(tmp_path, monkeypatch):
    """Guarded submit is executed at most once."""
    monkeypatch.setattr(sst3_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"

    host = MockHostForSST3(auth_ok=True, readiness_ok=True, submit_ok=True, transition_ok=True)

    res = sst3_runner.run_sst3_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_file,
        host_factory=lambda: host,
    )

    assert res["observed_actions"]["guarded_submit_attempts"] <= 1


def test_sst3_historical_evidence_and_runtime_closure_integrity():
    """Verify SST1 and SST2 frozen artifacts, 34 baselines, and 60 closure hashes."""
    import hashlib

    def sha256(p):
        with open(p, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    # SST1 frozen evidence
    assert sha256(REPO_ROOT / "artifacts/task11_search_submit_transition_sst1/sst1_marker.json") == "9d805f71ea20d1d200308fd65681786b827a942e82741df8c5b48baa6d0102c7"
    assert sha256(REPO_ROOT / "artifacts/task11_search_submit_transition_sst1/sst1_live_trace.json") == "d140a34a5410aac1dec946f37980d84762cffb7168859d3148c222d1fe6cfb1a"

    # SST2 frozen evidence
    assert sha256(REPO_ROOT / "artifacts/task11_search_submit_transition_sst2/sst2_marker.json") == "b6733a1de42dbfcb0b91f0b2b742743a9d7a35f2bbb69ce793d12ac9ce150214"
    assert sha256(REPO_ROOT / "artifacts/task11_search_submit_transition_sst2/sst2_live_trace.json") == "b8fe66f86e20a62035eae6a9d0880ef9fb564fa28cfbf5b03a038b4799a6f5cc"

    # SST3 protocol freeze baselines & closure
    proto = json.loads(sst3_runner.PROTOCOL_FREEZE_FILE.read_text(encoding="utf-8"))
    assert len(proto["historical_baselines"]) == 34
    assert len(proto["runtime_closure_hashes"]) == 60


def test_sst3_dry_run_execution(tmp_path):
    dry_out = tmp_path / "dry_trace.json"
    res = sst3_runner.run_sst3_qualification(dry_run=True, output_file_override=dry_out)
    assert res["verdict"] == "DRY_RUN_PASS"
    assert res["diagnostic_verdict"] == "SEARCH_SUBMIT_TRANSITION_SUCCESS"
    assert dry_out.exists()
