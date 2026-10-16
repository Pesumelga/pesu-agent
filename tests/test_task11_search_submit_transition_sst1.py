# -*- coding: utf-8 -*-
"""Comprehensive Unit & Offline Tests for SST1 Execution Hardening.

Required coverage:
- Exact HEAD / runner / protocol identity enforcement via authorization manifest
- Atomic marker creation and concurrent invocation rejection
- Bounded read-only readiness success and timeout
- Guarded submit at most once
- Unsafe or ambiguous candidate rejection
- Search result transition success and timeout (with result count 0 vs >= 1)
- Sensitive-data sanitization
- Historical frozen evidence integrity (34 baselines, 60 runtime closure hashes)
- Static AST safety contracts
"""

from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

import scripts.diagnose_task11_search_submit_transition_sst1 as sst1_runner


class MockHostForSST1:
    """Mock host simulating WebView JS evaluation for offline SST1 testing."""
    def __init__(
        self,
        auth_ok: bool = True,
        input_ready: bool = True,
        query_entry_ok: bool = True,
        readiness_ok: bool = True,
        submit_ok: bool = True,
        transition_ok: bool = True,
        result_count: int = 0,
    ):
        self.auth_ok = auth_ok
        self.input_ready = input_ready
        self.query_entry_ok = query_entry_ok
        self.readiness_ok = readiness_ok
        self.submit_ok = submit_ok
        self.transition_ok = transition_ok
        self.result_count = result_count
        self.stopped = False
        self.calls: list[str] = []

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def evaluate_javascript(self, script: str):
        self.calls.append(script)
        if "isLoginScreen" in script:
            return {"authenticated": self.auth_ok, "client_ready": self.auth_ok}
        if "searchButtons" in script:
            return {"activated": True}
        if "searchModalCandidates" in script:
            return {"input_ready": self.input_ready}
        if "setContents" in script:
            return {"ok": self.query_entry_ok, "query_length": len(sst1_runner.TARGET_QUERY)}
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
                    "surface_query_after": sst1_runner.TARGET_QUERY,
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
        "approved_git_commit": sst1_runner.get_git_commit(),
        "approved_runner_sha256": sst1_runner.get_file_sha256(Path(sst1_runner.__file__)),
        "approved_protocol_sha256": sst1_runner.get_file_sha256(sst1_runner.PROTOCOL_FREEZE_FILE),
        "authorized_at_utc": "2026-10-16T00:00:00Z",
        "authorization_scope": "TEST_OFFLINE",
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def test_sst1_protocol_freeze_and_baselines_integrity():
    assert sst1_runner.PROTOCOL_FREEZE_FILE.exists()
    proto = json.loads(sst1_runner.PROTOCOL_FREEZE_FILE.read_text(encoding="utf-8"))
    assert proto["protocol"] == "TASK_11_SEARCH_SUBMIT_TRANSITION_SST1"
    assert len(proto["historical_baselines"]) == 34
    assert len(proto["runtime_closure_hashes"]) == 60


def test_sst1_static_safety_contracts():
    ok, errors = sst1_runner.verify_static_safety_contracts()
    assert ok is True, f"Static safety errors: {errors}"


def test_sst1_dry_run_execution(tmp_path):
    dry_out = tmp_path / "dry_trace.json"
    res = sst1_runner.run_sst1_qualification(dry_run=True, output_file_override=dry_out)
    assert res["verdict"] == "DRY_RUN_PASS"
    assert res["diagnostic_verdict"] == "SEARCH_SUBMIT_TRANSITION_SUCCESS"
    assert res["checks"]["query_entry_executed"] is True
    assert res["checks"]["guarded_submit_executed"] is True
    assert res["checks"]["result_surface_confirmed"] is True
    assert dry_out.exists()


def test_sst1_exact_authorization_manifest_enforcement(tmp_path, monkeypatch):
    monkeypatch.setattr(sst1_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"

    # Case 1: Missing authorization file
    missing_auth = tmp_path / "non_existent_auth.json"
    res1 = sst1_runner.run_sst1_qualification(
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
        "approved_runner_sha256": sst1_runner.get_file_sha256(Path(sst1_runner.__file__)),
        "approved_protocol_sha256": sst1_runner.get_file_sha256(sst1_runner.PROTOCOL_FREEZE_FILE),
    }), encoding="utf-8")
    res2 = sst1_runner.run_sst1_qualification(
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
        "approved_git_commit": sst1_runner.get_git_commit(),
        "approved_runner_sha256": "badrunnerhash" * 5,
        "approved_protocol_sha256": sst1_runner.get_file_sha256(sst1_runner.PROTOCOL_FREEZE_FILE),
    }), encoding="utf-8")
    res3 = sst1_runner.run_sst1_qualification(
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
        "approved_git_commit": sst1_runner.get_git_commit(),
        "approved_runner_sha256": sst1_runner.get_file_sha256(Path(sst1_runner.__file__)),
        "approved_protocol_sha256": "badprotohash" * 5,
    }), encoding="utf-8")
    res4 = sst1_runner.run_sst1_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_bad_proto,
    )
    assert res4["verdict"] == "FAIL_CLOSED"
    assert res4["diagnostic_verdict"] == "UNAUTHORIZED_PROTOCOL_SHA"


def test_sst1_atomic_marker_and_concurrency_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(sst1_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")

    # Case 1: Marker already exists -> reject
    marker = tmp_path / "marker.json"
    marker.write_text('{"consumed": true}', encoding="utf-8")
    trace = tmp_path / "trace.json"
    res1 = sst1_runner.run_sst1_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        authorization_file_override=auth_file,
    )
    assert res1["verdict"] == "FAIL_CLOSED"
    assert res1["diagnostic_verdict"] == "ATTEMPT_ALREADY_EXISTS"

    # Case 2: Live trace already exists -> reject
    marker2 = tmp_path / "marker2.json"
    trace2 = tmp_path / "trace2.json"
    trace2.write_text('{"previous_run": true}', encoding="utf-8")
    res2 = sst1_runner.run_sst1_qualification(
        dry_run=False,
        marker_file_override=marker2,
        output_file_override=trace2,
        authorization_file_override=auth_file,
    )
    assert res2["verdict"] == "FAIL_CLOSED"
    assert res2["diagnostic_verdict"] == "LIVE_TRACE_ALREADY_EXISTS"

    # Case 3: Concurrent atomic creation
    concurrent_marker = tmp_path / "concurrent_marker.json"
    success_count = 0
    failure_count = 0

    def try_atomic_create():
        try:
            fd = os.open(str(concurrent_marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
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


def test_sst1_bounded_readiness_observation_success_and_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(sst1_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")

    # Success Case: readiness converges
    marker_ok = tmp_path / "m_ok.json"
    trace_ok = tmp_path / "t_ok.json"
    host_ok = MockHostForSST1(readiness_ok=True, submit_ok=True, transition_ok=True)
    res_ok = sst1_runner.run_sst1_qualification(
        dry_run=False,
        marker_file_override=marker_ok,
        output_file_override=trace_ok,
        authorization_file_override=auth_file,
        host_factory=lambda: host_ok,
    )
    assert res_ok["verdict"] == "QUALIFICATION_PASS"
    assert res_ok["checks"]["readiness_before_submit_converged"] is True
    assert res_ok["observed_actions"]["guarded_submit_attempts"] == 1

    # Timeout Case: readiness never converges (flags unsettled or candidate mismatch)
    marker_fail = tmp_path / "m_fail.json"
    trace_fail = tmp_path / "t_fail.json"
    host_fail = MockHostForSST1(readiness_ok=False, submit_ok=True, transition_ok=True)

    orig_r_timeout = sst1_runner.READINESS_OBSERVATION_TIMEOUT_MS
    orig_r_polls = sst1_runner.MAX_READINESS_POLLS
    sst1_runner.READINESS_OBSERVATION_TIMEOUT_MS = 200
    sst1_runner.MAX_READINESS_POLLS = 2

    try:
        res_fail = sst1_runner.run_sst1_qualification(
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
        sst1_runner.READINESS_OBSERVATION_TIMEOUT_MS = orig_r_timeout
        sst1_runner.MAX_READINESS_POLLS = orig_r_polls


def test_sst1_guarded_submit_at_most_once_and_guard_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(sst1_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")

    marker = tmp_path / "m_guard.json"
    trace = tmp_path / "t_guard.json"
    host = MockHostForSST1(readiness_ok=True, submit_ok=False, transition_ok=True)

    res = sst1_runner.run_sst1_qualification(
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


def test_sst1_result_transition_success_zero_and_nonzero_results(tmp_path, monkeypatch):
    monkeypatch.setattr(sst1_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")

    # Zero results is still a valid result surface transition
    m0 = tmp_path / "m0.json"
    t0 = tmp_path / "t0.json"
    host0 = MockHostForSST1(readiness_ok=True, submit_ok=True, transition_ok=True, result_count=0)
    res0 = sst1_runner.run_sst1_qualification(
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
    host5 = MockHostForSST1(readiness_ok=True, submit_ok=True, transition_ok=True, result_count=5)
    res5 = sst1_runner.run_sst1_qualification(
        dry_run=False,
        marker_file_override=m5,
        output_file_override=t5,
        authorization_file_override=auth_file,
        host_factory=lambda: host5,
    )
    assert res5["verdict"] == "QUALIFICATION_PASS"
    assert res5["transition_telemetry"]["result_root_count"] == 5


def test_sst1_transition_timeout_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sst1_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    auth_file = _make_valid_auth_file(tmp_path / "auth.json")

    marker = tmp_path / "m_tt.json"
    trace = tmp_path / "t_tt.json"
    host = MockHostForSST1(readiness_ok=True, submit_ok=True, transition_ok=False)

    orig_t_timeout = sst1_runner.TRANSITION_OBSERVATION_TIMEOUT_MS
    orig_t_polls = sst1_runner.MAX_TRANSITION_OBSERVATION_POLLS
    sst1_runner.TRANSITION_OBSERVATION_TIMEOUT_MS = 200
    sst1_runner.MAX_TRANSITION_OBSERVATION_POLLS = 2

    try:
        res = sst1_runner.run_sst1_qualification(
            dry_run=False,
            marker_file_override=marker,
            output_file_override=trace,
            authorization_file_override=auth_file,
            host_factory=lambda: host,
        )
        assert res["verdict"] == "FAIL_CLOSED"
        assert res["diagnostic_verdict"] == "TRANSITION_TIMED_OUT"
    finally:
        sst1_runner.TRANSITION_OBSERVATION_TIMEOUT_MS = orig_t_timeout
        sst1_runner.MAX_TRANSITION_OBSERVATION_POLLS = orig_t_polls


def test_sst1_sensitive_data_sanitization():
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
    sanitized = sst1_runner.sanitize_trace_data(raw_payload)

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
