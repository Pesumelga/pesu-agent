# -*- coding: utf-8 -*-
"""Unit & Integration Tests for SST1 (Single-Shot Guarded Search Submit -> Result Transition).

Verifies:
1. Protocol freeze integrity & 34 baselines match.
2. Static AST safety invariants (0 message posts, 0 Slackbot calls, 0 scroll mutations).
3. Dry-run execution generates valid artifact and returns DRY_RUN_PASS.
4. One-shot pre-marker hard gate rejects re-entry.
5. Mocked live branch: guarded submission & transition success.
6. Mocked live branch: guard rejection fails closed.
7. Mocked live branch: transition timeout fails closed.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

import scripts.diagnose_task11_search_submit_transition_sst1 as sst1_runner


class MockHostForSST1:
    def __init__(self, submit_ok: bool = True, transition_ok: bool = True):
        self.submit_ok = submit_ok
        self.transition_ok = transition_ok
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def evaluate_javascript(self, script: str):
        if "isLoginScreen" in script:
            return {"authenticated": True, "client_ready": True}
        if "searchButtons" in script:
            return {"activated": True}
        if "searchModalCandidates" in script:
            return {"input_ready": True}
        if "setContents" in script:
            return {"ok": True, "query_length": len(sst1_runner.TARGET_QUERY)}
        if "normalizeQuery" in script or "isExactSafeKeywordSearch" in script:
            if self.submit_ok:
                return {
                    "ok": True,
                    "report": {
                        "submit_succeeded": True,
                        "submit_method": "SafeSearchSuggestionDOMItem.click[index=0]"
                    }
                }
            else:
                return {"error": "SAFE_CANDIDATE_QUERY_MISMATCH", "report": {"submit_succeeded": False}}
        if "skeletons" in script or "search_view" in script or "surfaceQueryAfter" in script:
            if self.transition_ok:
                return {
                    "search_view_present": True,
                    "loading_indicator": False,
                    "surface_query_after": sst1_runner.TARGET_QUERY,
                    "result_items": [{"id": "item1"}, {"id": "item2"}]
                }
            else:
                return {
                    "search_view_present": False,
                    "loading_indicator": True,
                    "surface_query_after": None,
                    "result_items": []
                }
        return None


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


def test_sst1_one_shot_marker_hard_gate(tmp_path):
    marker = tmp_path / "sst1_marker.json"
    marker.write_text('{"consumed": true}', encoding="utf-8")
    res = sst1_runner.run_sst1_qualification(marker_file_override=marker)
    assert res["verdict"] == "FAIL_CLOSED"
    assert res["diagnostic_verdict"] == "ATTEMPT_ALREADY_EXISTS"


def test_sst1_mocked_live_success(tmp_path):
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"
    host = MockHostForSST1(submit_ok=True, transition_ok=True)

    res = sst1_runner.run_sst1_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        host_factory=lambda: host,
    )
    assert res["verdict"] == "QUALIFICATION_PASS"
    assert res["diagnostic_verdict"] == "SEARCH_SUBMIT_TRANSITION_SUCCESS"
    assert res["live_action_inventory"]["query_entries"] == 1
    assert res["live_action_inventory"]["guarded_submit_attempts"] == 1
    assert res["live_action_inventory"]["guarded_submit_successes"] == 1
    assert res["live_action_inventory"]["slack_messages_sent"] == 0
    assert res["live_action_inventory"]["slackbot_invocations"] == 0
    assert trace.exists()


def test_sst1_mocked_guard_block_fails_closed(tmp_path):
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"
    host = MockHostForSST1(submit_ok=False, transition_ok=True)

    res = sst1_runner.run_sst1_qualification(
        dry_run=False,
        marker_file_override=marker,
        output_file_override=trace,
        host_factory=lambda: host,
    )
    assert res["verdict"] == "FAIL_CLOSED"
    assert res["diagnostic_verdict"] == "SEARCH_GUARD_BLOCKED_SUBMISSION"
    assert res["live_action_inventory"]["guarded_submit_attempts"] == 1
    assert res["live_action_inventory"]["guarded_submit_successes"] == 0


def test_sst1_mocked_transition_timeout_fails_closed(tmp_path):
    marker = tmp_path / "marker.json"
    trace = tmp_path / "trace.json"
    host = MockHostForSST1(submit_ok=True, transition_ok=False)

    # Monkeypatch timeout constants for fast test execution
    orig_timeout = sst1_runner.TRANSITION_OBSERVATION_TIMEOUT_MS
    orig_polls = sst1_runner.MAX_TRANSITION_OBSERVATION_POLLS
    sst1_runner.TRANSITION_OBSERVATION_TIMEOUT_MS = 200
    sst1_runner.MAX_TRANSITION_OBSERVATION_POLLS = 2

    try:
        res = sst1_runner.run_sst1_qualification(
            dry_run=False,
            marker_file_override=marker,
            output_file_override=trace,
            host_factory=lambda: host,
        )
        assert res["verdict"] == "FAIL_CLOSED"
        assert res["diagnostic_verdict"] == "TRANSITION_TIMED_OUT"
    finally:
        sst1_runner.TRANSITION_OBSERVATION_TIMEOUT_MS = orig_timeout
        sst1_runner.MAX_TRANSITION_OBSERVATION_POLLS = orig_polls
