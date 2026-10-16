# -*- coding: utf-8 -*-
"""Comprehensive Unit & Offline Tests for SST2 Execution Hardening.

Required coverage:
- Real PesuWebViewHost constructor signature compatibility (user_data_folder=str(DIRECT_SLACK_PROFILE_PATH))
- Real PesuWebViewHost start/evaluate/cleanup interface contracts
- Production runner branch offline stub integration (executing the else: host = PesuWebViewHost(...) branch)
- Exact HEAD / runner / protocol identity enforcement via authorization manifest
- SST2 scope enforcement (SST2_LIVE_SINGLE_SHOT)
- Atomic marker creation and concurrent invocation rejection
- Bounded read-only readiness success and timeout
- Guarded submit at most once and guard rejection
- Search result transition success and timeout (with result count 0 vs >= 1)
- Sensitive-data sanitization
- Historical frozen evidence integrity (34 baselines, 60 runtime closure hashes)
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
import scripts.diagnose_task11_search_submit_transition_sst2 as sst2_runner


class MockHostForSST2:
    """Mock host simulating WebView JS evaluation for offline SST2 testing."""
    def __init__(
        self,
        auth_ok: bool = True,
        input_ready: bool = True,
        query_entry_ok: bool = True,
        readiness_ok: bool = True,
        submit_ok: bool = True,
        transition_ok: bool = True,
        result_count: int = 0,
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
        self.stopped = False
        self.destroyed = False
        self.started = False
        self.calls: list[str] = []
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
        if "isLoginScreen" in script:
            return {"authenticated": self.auth_ok, "client_ready": self.auth_ok}
        if "searchButtons" in script:
            return {"activated": True}
        if "searchModalCandidates" in script:
            return {"input_ready": self.input_ready}
        if "setContents" in script:
            return {"ok": self.query_entry_ok, "query_length": len(sst2_runner.TARGET_QUERY)}
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
                    "reasons": ["INTERNAL_FLAGS_NOT_SETTLED", "SAFE_CANDIDATE_QUERY_MISMATCH"],
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
                    "error": "SAFE_CANDIDATE_QUERY_MISMATCH",
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
                    "surface_query_after": sst2_runner.TARGET_QUERY,
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
        "approved_git_commit": sst2_runner.get_git_commit(),
        "approved_runner_sha256": sst2_runner.get_file_sha256(Path(sst2_runner.__file__)),
        "approved_protocol_sha256": sst2_runner.get_file_sha256(sst2_runner.PROTOCOL_FREEZE_FILE),
        "authorized_at_utc": "2026-10-16T12:00:00Z",
        "authorization_scope": "SST2_LIVE_SINGLE_SHOT",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Section 4: Mandatory Real Host Constructor & Interface Verification
# ---------------------------------------------------------------------------

def test_real_host_constructor_signature_compatibility():
    """Verify PesuWebViewHost.__init__ signature accepts user_data_folder."""
    sig = inspect.signature(PesuWebViewHost.__init__)

    # Verify user_data_folder is a valid parameter
    assert "user_data_folder" in sig.parameters

    # Verify binding with user_data_folder=str(DIRECT_SLACK_PROFILE_PATH)
    bound_minimal = sig.bind(None, user_data_folder=str(DIRECT_SLACK_PROFILE_PATH))
    assert bound_minimal.arguments["user_data_folder"] == str(DIRECT_SLACK_PROFILE_PATH)

    # Verify binding with initial_url and title as used in runner
    bound_full = sig.bind(
        None,
        user_data_folder=str(DIRECT_SLACK_PROFILE_PATH),
        initial_url="https://app.slack.com/client",
        title="PESU Task 11 SST2 Search Submit Transition",
    )
    assert bound_full.arguments["user_data_folder"] == str(DIRECT_SLACK_PROFILE_PATH)
    assert bound_full.arguments["initial_url"] == "https://app.slack.com/client"


def test_real_host_interface_contracts_and_adapter_compatibility():
    """Verify PesuWebViewHost methods and adapter helpers (_evaluate_host_js, _cleanup_host)."""
    # Verify real class start signature
    sig_start = inspect.signature(PesuWebViewHost.start)
    assert "timeout" in sig_start.parameters

    # Verify real class has evaluate_js and destroy
    assert hasattr(PesuWebViewHost, "evaluate_js")
    assert hasattr(PesuWebViewHost, "destroy")

    # Document actual absence of evaluate_javascript and stop on raw class
    assert not hasattr(PesuWebViewHost, "evaluate_javascript")
    assert not hasattr(PesuWebViewHost, "stop")

    # Verify _evaluate_host_js adapter handles evaluate_js
    class DummyWithEvaluateJs:
        def evaluate_js(self, s): return {"ok": True, "js": s}

    assert sst2_runner._evaluate_host_js(DummyWithEvaluateJs(), "test()") == {"ok": True, "js": "test()"}

    # Verify _evaluate_host_js adapter handles evaluate_javascript
    class DummyWithEvaluateJavascript:
        def evaluate_javascript(self, s): return {"ok": True, "js_legacy": s}

    assert sst2_runner._evaluate_host_js(DummyWithEvaluateJavascript(), "test()") == {"ok": True, "js_legacy": "test()"}

    # Verify _cleanup_host adapter handles destroy
    class DummyWithDestroy:
        destroyed = False
        def destroy(self): self.destroyed = True

    d1 = DummyWithDestroy()
    sst2_runner._cleanup_host(d1)
    assert d1.destroyed is True

    # Verify _cleanup_host adapter handles stop
    class DummyWithStop:
        stopped = False
        def stop(self): self.stopped = True

    d2 = DummyWithStop()
    sst2_runner._cleanup_host(d2)
    assert d2.stopped is True


def test_production_runner_branch_with_offline_stub(tmp_path, monkeypatch):
    """Executes the runner's ACTUAL 'else: host = PesuWebViewHost(...)' branch without host_factory."""
    monkeypatch.setattr(sst2_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))

    instantiated_instances: list[MockHostForSST2] = []

    def stub_constructor(*args, **kwargs):
        inst = MockHostForSST2(
            auth_ok=True,
            input_ready=True,
            query_entry_ok=True,
            readiness_ok=True,
            submit_ok=True,
            transition_ok=True,
            result_count=0,
            *args,
            **kwargs,
        )
        instantiated_instances.append(inst)
        return inst

    # Monkeypatch PesuWebViewHost in the runner module
    monkeypatch.setattr(sst2_runner, "PesuWebViewHost", stub_constructor)

    # Use isolated temp directories (NEVER real SST2 files)
    temp_auth = _make_valid_auth_file(tmp_path / "temp_auth.json")
    temp_marker = tmp_path / "temp_marker.json"
    temp_trace = tmp_path / "temp_trace.json"

    # Execute with host_factory=None to FORCE execution of the production constructor branch!
    res = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=temp_marker,
        output_file_override=temp_trace,
        authorization_file_override=temp_auth,
        host_factory=None,  # Crucial: Must be None to test production instantiation branch!
    )

    # Verify execution succeeded without TypeError or AttributeError
    assert res["verdict"] == "QUALIFICATION_PASS"
    assert res["diagnostic_verdict"] == "SEARCH_SUBMIT_TRANSITION_SUCCESS"
    assert res["error"] is None

    # Verify exactly one stub instance was created through the production constructor branch
    assert len(instantiated_instances) == 1
    stub = instantiated_instances[0]

    # Verify exact constructor kwargs received by PesuWebViewHost
    assert "user_data_folder" in stub.init_kwargs
    assert stub.init_kwargs["user_data_folder"] == str(DIRECT_SLACK_PROFILE_PATH)
    assert stub.init_kwargs.get("initial_url") == "https://app.slack.com/client"

    # Verify lifecycle methods were invoked
    assert stub.started is True
    assert stub.destroyed is True or stub.stopped is True
    assert len(stub.calls) >= 5

    # Verify real SST2 artifacts were NOT touched
    assert not (REPO_ROOT / "artifacts" / "task11_search_submit_transition_sst2" / "sst2_marker.json").exists()
    assert not (REPO_ROOT / "artifacts" / "task11_search_submit_transition_sst2" / "sst2_authorization.json").exists()
    assert not (REPO_ROOT / "artifacts" / "task11_search_submit_transition_sst2" / "sst2_live_trace.json").exists()


# ---------------------------------------------------------------------------
# Section 5: SST2 Offline Qualification Tests
# ---------------------------------------------------------------------------

def test_sst2_protocol_freeze_and_baselines_integrity():
    assert sst2_runner.PROTOCOL_FREEZE_FILE.exists()
    proto = json.loads(sst2_runner.PROTOCOL_FREEZE_FILE.read_text(encoding="utf-8"))
    assert proto["protocol"] == "TASK_11_SEARCH_SUBMIT_TRANSITION_SST2"
    assert proto["task_id"] == "TASK_11_SEARCH_SUBMIT_TRANSITION_SST2"
    assert proto["diagnostic_run"] == "SST2"
    assert len(proto["historical_baselines"]) == 34
    assert len(proto["runtime_closure_hashes"]) == 60


def test_sst2_static_safety_contracts():
    ok, errors = sst2_runner.verify_static_safety_contracts()
    assert ok is True, f"Static safety errors: {errors}"


def test_sst2_dry_run_execution(tmp_path):
    dry_out = tmp_path / "dry_trace.json"
    res = sst2_runner.run_sst2_qualification(dry_run=True, output_file_override=dry_out)
    assert res["verdict"] == "DRY_RUN_PASS"
    assert res["diagnostic_verdict"] == "SEARCH_SUBMIT_TRANSITION_SUCCESS"
    assert res["checks"]["query_entry_executed"] is True
    assert res["checks"]["guarded_submit_executed"] is True
    assert res["checks"]["result_surface_confirmed"] is True
    assert dry_out.exists()


def test_sst2_exact_authorization_manifest_enforcement(tmp_path, monkeypatch):
    monkeypatch.setattr(sst2_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"

    # Case 1: Missing authorization file
    missing_auth = tmp_path / "non_existent_auth.json"
    res1 = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=missing_auth,
    )
    assert res1["verdict"] == "FAIL_CLOSED"
    assert res1["diagnostic_verdict"] == "MISSING_AUTHORIZATION_FILE"

    # Case 2: Mismatched Git HEAD
    auth_bad_git = tmp_path / "bad_git_auth.json"
    auth_bad_git.write_text(json.dumps({
        "approved_git_commit": "0000000000000000000000000000000000000000",
        "approved_runner_sha256": sst2_runner.get_file_sha256(Path(sst2_runner.__file__)),
        "approved_protocol_sha256": sst2_runner.get_file_sha256(sst2_runner.PROTOCOL_FREEZE_FILE),
        "authorization_scope": "SST2_LIVE_SINGLE_SHOT",
    }), encoding="utf-8")
    res2 = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_bad_git,
    )
    assert res2["verdict"] == "FAIL_CLOSED"
    assert res2["diagnostic_verdict"] == "UNAUTHORIZED_GIT_COMMIT"

    # Case 3: Mismatched Runner SHA
    auth_bad_runner = tmp_path / "bad_runner_auth.json"
    auth_bad_runner.write_text(json.dumps({
        "approved_git_commit": sst2_runner.get_git_commit(),
        "approved_runner_sha256": "badrunnerhash" * 5,
        "approved_protocol_sha256": sst2_runner.get_file_sha256(sst2_runner.PROTOCOL_FREEZE_FILE),
        "authorization_scope": "SST2_LIVE_SINGLE_SHOT",
    }), encoding="utf-8")
    res3 = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_bad_runner,
    )
    assert res3["verdict"] == "FAIL_CLOSED"
    assert res3["diagnostic_verdict"] == "UNAUTHORIZED_RUNNER_SHA"

    # Case 4: Mismatched Protocol SHA
    auth_bad_proto = tmp_path / "bad_proto_auth.json"
    auth_bad_proto.write_text(json.dumps({
        "approved_git_commit": sst2_runner.get_git_commit(),
        "approved_runner_sha256": sst2_runner.get_file_sha256(Path(sst2_runner.__file__)),
        "approved_protocol_sha256": "badprotohash" * 5,
        "authorization_scope": "SST2_LIVE_SINGLE_SHOT",
    }), encoding="utf-8")
    res4 = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_bad_proto,
    )
    assert res4["verdict"] == "FAIL_CLOSED"
    assert res4["diagnostic_verdict"] == "UNAUTHORIZED_PROTOCOL_SHA"

    # Case 5: SST1 scope in SST2 authorization is rejected
    auth_sst1_scope = tmp_path / "sst1_scope_auth.json"
    auth_sst1_scope.write_text(json.dumps({
        "approved_git_commit": sst2_runner.get_git_commit(),
        "approved_runner_sha256": sst2_runner.get_file_sha256(Path(sst2_runner.__file__)),
        "approved_protocol_sha256": sst2_runner.get_file_sha256(sst2_runner.PROTOCOL_FREEZE_FILE),
        "authorization_scope": "SST1_LIVE_SINGLE_SHOT",
    }), encoding="utf-8")
    res5 = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_sst1_scope,
    )
    assert res5["verdict"] == "FAIL_CLOSED"
    assert res5["diagnostic_verdict"] == "UNAUTHORIZED_SCOPE"


def test_sst2_atomic_marker_and_concurrency_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(sst2_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")
    marker_file = tmp_path / "atomic_marker.json"

    # 1. First execution writes marker and succeeds
    trace1 = tmp_path / "trace1.json"
    host1 = MockHostForSST2(readiness_ok=True, submit_ok=True, transition_ok=True)
    res1 = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=marker_file,
        output_file_override=trace1,
        authorization_file_override=auth_file,
        host_factory=lambda: host1,
    )
    assert res1["verdict"] == "QUALIFICATION_PASS"
    assert res1["checks"]["attempt_marker_written"] is True
    assert marker_file.exists()

    # 2. Second execution with existing marker fails closed with ATTEMPT_ALREADY_EXISTS
    trace2 = tmp_path / "trace2.json"
    host2 = MockHostForSST2(readiness_ok=True, submit_ok=True, transition_ok=True)
    res2 = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=marker_file,
        output_file_override=trace2,
        authorization_file_override=auth_file,
        host_factory=lambda: host2,
    )
    assert res2["verdict"] == "FAIL_CLOSED"
    assert res2["diagnostic_verdict"] == "ATTEMPT_ALREADY_EXISTS"

    # 3. Concurrent multi-thread marker creation race: exactly one wins
    concurrency_marker = tmp_path / "concurrent_marker.json"

    def try_atomic_create():
        try:
            fd = os.open(str(concurrency_marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write("claimed")
            return True
        except FileExistsError:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(try_atomic_create) for _ in range(8)]
        results = [f.result() for f in futures]

    assert results.count(True) == 1, "Exactly one thread must acquire atomic marker"
    assert results.count(False) == 7, "All competing threads must fail with FileExistsError"


def test_sst2_bounded_readiness_observation_success_and_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(sst2_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")

    # Success Case: readiness converges
    marker_ok = tmp_path / "m_ok.json"
    trace_ok = tmp_path / "t_ok.json"
    host_ok = MockHostForSST2(readiness_ok=True, submit_ok=True, transition_ok=True)
    res_ok = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=marker_ok,
        output_file_override=trace_ok,
        authorization_file_override=auth_file,
        host_factory=lambda: host_ok,
    )
    assert res_ok["verdict"] == "QUALIFICATION_PASS"
    assert res_ok["checks"]["readiness_before_submit_converged"] is True
    assert res_ok["observed_actions"]["guarded_submit_attempts"] == 1

    # Timeout Case: readiness never converges
    marker_fail = tmp_path / "m_fail.json"
    trace_fail = tmp_path / "t_fail.json"
    host_fail = MockHostForSST2(readiness_ok=False, submit_ok=True, transition_ok=True)

    orig_r_timeout = sst2_runner.READINESS_OBSERVATION_TIMEOUT_MS
    orig_r_polls = sst2_runner.MAX_READINESS_POLLS
    sst2_runner.READINESS_OBSERVATION_TIMEOUT_MS = 200
    sst2_runner.MAX_READINESS_POLLS = 2

    try:
        res_fail = sst2_runner.run_sst2_qualification(
            dry_run=False,
            marker_file_override=marker_fail,
            output_file_override=trace_fail,
            authorization_file_override=auth_file,
            host_factory=lambda: host_fail,
        )
        assert res_fail["verdict"] == "FAIL_CLOSED"
        assert res_fail["diagnostic_verdict"] == "READINESS_CONVERGENCE_TIMED_OUT"
        assert res_fail["observed_actions"]["guarded_submit_attempts"] == 0, "Guarded submit MUST NOT execute if readiness timed out"
    finally:
        sst2_runner.READINESS_OBSERVATION_TIMEOUT_MS = orig_r_timeout
        sst2_runner.MAX_READINESS_POLLS = orig_r_polls


def test_sst2_guarded_submit_at_most_once_and_guard_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(sst2_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")

    marker = tmp_path / "m_guard.json"
    trace = tmp_path / "t_guard.json"
    host = MockHostForSST2(readiness_ok=True, submit_ok=False, transition_ok=True)

    res = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_file,
        host_factory=lambda: host,
    )
    assert res["verdict"] == "FAIL_CLOSED"
    assert res["diagnostic_verdict"] == "SEARCH_GUARD_BLOCKED_SUBMISSION"
    assert res["observed_actions"]["guarded_submit_attempts"] == 1
    assert res["observed_actions"]["guarded_submit_successes"] == 0
    assert res["checks"]["transition_observation_executed"] is False, "Transition must not execute after guard blocks"


def test_sst2_result_transition_success_zero_and_nonzero_results(tmp_path, monkeypatch):
    monkeypatch.setattr(sst2_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")

    # Zero results is still a valid result surface transition
    m0 = tmp_path / "m0.json"
    t0 = tmp_path / "t0.json"
    host0 = MockHostForSST2(readiness_ok=True, submit_ok=True, transition_ok=True, result_count=0)
    res0 = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=m0,
        output_file_override=t0,
        authorization_file_override=auth_file,
        host_factory=lambda: host0,
    )
    assert res0["verdict"] == "QUALIFICATION_PASS"
    assert res0["transition_telemetry"]["result_root_count"] == 0

    # Non-zero results is also valid
    m5 = tmp_path / "m5.json"
    t5 = tmp_path / "t5.json"
    host5 = MockHostForSST2(readiness_ok=True, submit_ok=True, transition_ok=True, result_count=5)
    res5 = sst2_runner.run_sst2_qualification(
        dry_run=False,
        marker_file_override=m5,
        output_file_override=t5,
        authorization_file_override=auth_file,
        host_factory=lambda: host5,
    )
    assert res5["verdict"] == "QUALIFICATION_PASS"
    assert res5["transition_telemetry"]["result_root_count"] == 5


def test_sst2_transition_timeout_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sst2_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")

    marker = tmp_path / "m_tt.json"
    trace = tmp_path / "t_tt.json"
    host = MockHostForSST2(readiness_ok=True, submit_ok=True, transition_ok=False)

    orig_t_timeout = sst2_runner.TRANSITION_OBSERVATION_TIMEOUT_MS
    orig_t_polls = sst2_runner.MAX_TRANSITION_OBSERVATION_POLLS
    sst2_runner.TRANSITION_OBSERVATION_TIMEOUT_MS = 200
    sst2_runner.MAX_TRANSITION_OBSERVATION_POLLS = 2

    try:
        res = sst2_runner.run_sst2_qualification(
            dry_run=False,
            marker_file_override=marker,
            output_file_override=trace,
            authorization_file_override=auth_file,
            host_factory=lambda: host,
        )
        assert res["verdict"] == "FAIL_CLOSED"
        assert res["diagnostic_verdict"] == "TRANSITION_TIMED_OUT"
    finally:
        sst2_runner.TRANSITION_OBSERVATION_TIMEOUT_MS = orig_t_timeout
        sst2_runner.MAX_TRANSITION_OBSERVATION_POLLS = orig_t_polls


def test_sst2_sensitive_data_sanitization():
    raw_payload = {
        "slack_token": ('xox' + 'b-123456789012-abcdefghijklmn'),
        "auth_header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "url": "https://admin:supersecret@slack.com/api/test",
        "nested": {
            "password": "my_secret_password",
            "safe_field": "public_data",
            "body_text": "Sensitive business communication text here",
        },
        "raw_string": ('Error occurred with token ' + 'xox' + 'p-987654321098-zyxwvutsrqpo' + ' during call'),
        "bearer_log": "Authorization: Bearer my_secret_bearer_token_string_here",
    }
    sanitized = sst2_runner.sanitize_trace_data(raw_payload)

    assert sanitized["slack_token"] == "[REDACTED_CREDENTIAL]"
    assert sanitized["auth_header"] == "[REDACTED_CREDENTIAL]"
    assert "[REDACTED_AUTH]@" in sanitized["url"]
    assert sanitized["nested"]["password"] == "[REDACTED_CREDENTIAL]"
    assert sanitized["nested"]["safe_field"] == "public_data"
    assert sanitized["nested"]["body_text"] == "[REDACTED_SENSITIVE_TEXT]"
    assert "[REDACTED_SLACK_TOKEN]" in sanitized["raw_string"]
    assert "xoxp-" not in sanitized["raw_string"]
    assert sanitized["bearer_log"] == "Authorization: [REDACTED_BEARER_TOKEN]"
    assert "my_secret_bearer" not in sanitized["bearer_log"]
