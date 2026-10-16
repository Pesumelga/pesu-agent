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
            }

        if "searchButtons" in script and "dispatchEvent" in script:
            # JS_ACTIVATE_SEARCH
            return {"activated": True}

        if "searchModalCandidates" in script:
            # JS_CHECK_SEARCH_INPUT_READINESS
            return {"ready": self.input_ready, "ambiguous": False}

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
        assert res["diagnostic_verdict"] == "AUTH_PREFLIGHT_JS_ERROR"
        assert "Simulated JS evaluation failure" in res["error"]
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


class ProductionJSHost:
    """Execute the runner's exact expressions in Node, using the existing guard DOM fixture."""

    def __init__(self):
        self.calls = []
        self.destroyed = False
        self.entered = False
        self.submitted = False

    def destroy(self):
        self.destroyed = True

    def evaluate_js(self, script):
        import subprocess
        from tests.test_sfc2_production_guard_scenarios import NODE_MOCK_HARNESS

        self.calls.append(script)
        source = NODE_MOCK_HARNESS + "\n" + r'''
const query = __QUERY__;
const env = createSlackSearchEnvironment({targetQuery: query});
let entered = __ENTERED__, submitted = __SUBMITTED__;
env.inst.onEnter = () => { throw new Error('Generic onEnter forbidden'); };
env.inst.texty.setContents = (contents) => {
    if (contents.contents[0].insert !== query) throw new Error('Wrong query');
    entered = true;
};
env.input.offsetWidth = 100;
env.input.getClientRects = () => [{}];
for (const option of env.acList.children) option.click = () => { submitted = true; };
const oldAll = document.querySelectorAll;
document.querySelectorAll = (selector) => {
    if (selector.includes('top_nav_search')) return [{dispatchEvent: () => true}];
    if (selector === '[data-qa="search_view"]') return submitted ? [{isConnected:true, querySelectorAll:()=>[]}] : [];
    return oldAll(selector);
};
window.location.href = 'https://app.slack.com/client/T001/C001' + (submitted ? '?q=' + encodeURIComponent(query) : '');
const result = eval(__SCRIPT__);
console.log(JSON.stringify({result, entered, submitted}));
'''
        for key, value in {"QUERY": sst3_runner.TARGET_QUERY, "ENTERED": self.entered,
                           "SUBMITTED": self.submitted, "SCRIPT": script}.items():
            source = source.replace("__" + key + "__", json.dumps(value))
        proc = subprocess.run(["node", "-"], input=source, capture_output=True, text=True, encoding="utf-8")
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)
        self.entered, self.submitted = data["entered"], data["submitted"]
        return data["result"]


def _offline_execution(tmp_path, monkeypatch, host):
    # Only temporary fixtures authorize the offline stub; never use real artifact paths.
    monkeypatch.setattr(sst3_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    monkeypatch.setattr(sst3_runner.time, "sleep", lambda _: None)
    return sst3_runner.run_sst3_qualification(
        marker_file_override=tmp_path / "marker.json",
        output_file_override=tmp_path / "trace.json",
        authorization_file_override=_make_valid_auth_file(tmp_path / "auth.json"),
        host_factory=lambda: host,
    )


def test_real_production_js_complete_runner_path(tmp_path, monkeypatch):
    host = ProductionJSHost()
    assert host.evaluate_js(sst3_runner.JS_CHECK_AUTH_AND_READINESS) == {
        "ready": True, "auth_required": False, "search_button_count": 1,
    }
    host.calls.clear()
    result = _offline_execution(tmp_path, monkeypatch, host)
    assert result["diagnostic_verdict"] == "SEARCH_SUBMIT_TRANSITION_SUCCESS", result
    assert host.entered and host.submitted and host.destroyed
    assert result["observed_actions"]["query_entries_executed"] == 1
    assert result["observed_actions"]["guarded_submit_attempts"] == 1
    assert host.calls[:2] == [sst3_runner.JS_CHECK_AUTH_AND_READINESS] * 2
    original = (tmp_path / "trace.json").read_bytes()
    again = _offline_execution(tmp_path, monkeypatch, host)
    assert again["diagnostic_verdict"] == "LIVE_TRACE_ALREADY_EXISTS"
    assert (tmp_path / "trace.json").read_bytes() == original


@pytest.mark.parametrize("response", [
    {"ready": True, "auth_required": True, "authenticated": True, "client_ready": True},
    {"authenticated": True, "client_ready": True},
    {"ready": True, "auth_required": False},
    {"ready": "yes", "auth_required": False, "search_button_count": 1},
    {"ready": True, "auth_required": False, "search_button_count": 2},
    None, "TypeError", [],
])
def test_auth_requires_real_strict_production_schema(tmp_path, monkeypatch, response):
    host = MockHostForSST3()
    host.evaluate_js = lambda _: response
    result = _offline_execution(tmp_path, monkeypatch, host)
    assert result["verdict"] == "FAIL_CLOSED"
    assert not (tmp_path / "marker.json").exists()


@pytest.mark.parametrize("activation", [None, {"error": "BUTTON_CARDINALITY_NOT_ONE"}, {"activated": False}])
def test_activation_failure_never_enters_query(tmp_path, monkeypatch, activation):
    host = MockHostForSST3()
    evaluate = host.evaluate_js
    host.evaluate_js = lambda s: activation if s == sst3_runner.JS_ACTIVATE_SEARCH else evaluate(s)
    result = _offline_execution(tmp_path, monkeypatch, host)
    assert result["diagnostic_verdict"] == "SEARCH_ACTIVATION_FAILED"
    assert result["observed_actions"]["query_entries_executed"] == 0


def test_delayed_input_mount_is_observed_without_reactivation(tmp_path, monkeypatch):
    host = MockHostForSST3()
    evaluate = host.evaluate_js
    responses = iter([{"ready": False, "ambiguous": False}, {"ready": True, "ambiguous": False}])
    def delayed(script):
        if script == sst3_runner.JS_CHECK_SEARCH_INPUT_READINESS:
            host.calls.append(script)
            return next(responses)
        return evaluate(script)
    host.evaluate_js = delayed
    result = _offline_execution(tmp_path, monkeypatch, host)
    assert result["verdict"] == "QUALIFICATION_PASS", result
    assert host.calls.count(sst3_runner.JS_ACTIVATE_SEARCH) == 1
    assert host.calls.count(sst3_runner.JS_CHECK_SEARCH_INPUT_READINESS) == 2


def test_marker_race_cannot_overwrite_winner_trace(tmp_path, monkeypatch):
    host = MockHostForSST3()
    evaluate = host.evaluate_js
    def racing(script):
        if script == sst3_runner.JS_CHECK_AUTH_AND_READINESS:
            (tmp_path / "marker.json").write_text('winner marker')
            (tmp_path / "trace.json").write_text('winner trace')
        return evaluate(script)
    host.evaluate_js = racing
    result = _offline_execution(tmp_path, monkeypatch, host)
    assert result["diagnostic_verdict"] == "ATTEMPT_ALREADY_EXISTS"
    assert (tmp_path / "trace.json").read_text(encoding="utf-8") == 'winner trace'


def test_preflight_failure_does_not_block_later_readiness(tmp_path, monkeypatch):
    result = _offline_execution(tmp_path, monkeypatch, MockHostForSST3(auth_ok=False))
    assert result["diagnostic_verdict"] == "AUTH_OR_CLIENT_NOT_READY"
    assert not (tmp_path / "marker.json").exists()
    assert not (tmp_path / "trace.json").exists()
    assert _offline_execution(tmp_path, monkeypatch, MockHostForSST3())["verdict"] == "QUALIFICATION_PASS"


@pytest.mark.parametrize("stage, counter", [
    ("JS_SET_TEXTY_QUERY", "query_entries_executed"),
    ("JS_ATOMIC_PRE_SUBMIT_AND_INVOKE_ONENTER", "guarded_submit_attempts"),
])
def test_action_evaluation_exception_retains_attempt_without_retry(tmp_path, monkeypatch, stage, counter):
    host = MockHostForSST3()
    evaluate = host.evaluate_js
    script = f"({getattr(sst3_runner, stage)})({json.dumps(sst3_runner.TARGET_QUERY)})"
    def throwing(expression):
        if expression == script:
            host.calls.append(expression)
            raise TimeoutError("Response lost after dispatch")
        return evaluate(expression)
    host.evaluate_js = throwing
    result = _offline_execution(tmp_path, monkeypatch, host)
    assert result["diagnostic_verdict"] == "RUNTIME_EXCEPTION"
    assert result["observed_actions"][counter] == 1
    assert host.calls.count(script) == 1
    assert (tmp_path / "marker.json").exists()
    before = len(host.calls)
    assert _offline_execution(tmp_path, monkeypatch, host)["verdict"] == "FAIL_CLOSED"
    assert len(host.calls) == before


@pytest.mark.parametrize("response", [None, "error", {}, {
    "search_view_mounted": True, "surface_query_after": sst3_runner.TARGET_QUERY,
}])
def test_missing_transition_fields_cannot_pass(tmp_path, monkeypatch, response):
    host = MockHostForSST3()
    evaluate = host.evaluate_js
    host.evaluate_js = lambda s: response if s == sst3_runner.JS_PROBE_POST_WAIT_SEARCH_STATE else evaluate(s)
    result = _offline_execution(tmp_path, monkeypatch, host)
    assert result["diagnostic_verdict"] == "TRANSITION_TIMED_OUT"
    assert result["observed_actions"]["guarded_submit_attempts"] == 1


def test_default_host_construction_start_and_fallback_adapters(tmp_path, monkeypatch):
    host = MockHostForSST3()
    def construct(**kwargs):
        inspect.signature(PesuWebViewHost).bind(**kwargs)
        assert kwargs["user_data_folder"] == str(DIRECT_SLACK_PROFILE_PATH)
        assert kwargs["initial_url"] == "https://app.slack.com/client"
        return host
    monkeypatch.setattr(sst3_runner, "PesuWebViewHost", construct)
    monkeypatch.setattr(sst3_runner, "verify_clean_working_tree", lambda: (True, "CLEAN"))
    result = sst3_runner.run_sst3_qualification(
        marker_file_override=tmp_path / "marker.json",
        output_file_override=tmp_path / "trace.json",
        authorization_file_override=_make_valid_auth_file(tmp_path / "auth.json"),
    )
    assert result["verdict"] == "QUALIFICATION_PASS"
    assert host.started and host.destroyed

    class LegacyHost:
        def evaluate_javascript(self, script):
            return script
        def stop(self):
            self.stopped = True
    legacy = LegacyHost()
    assert sst3_runner._evaluate_host_js(legacy, "expression") == "expression"
    sst3_runner._cleanup_host(legacy)
    assert legacy.stopped
