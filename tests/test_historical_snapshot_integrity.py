#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Historical Snapshot Integrity and Temporal Audit Suite.

Verifies:
1. Historical 34 baselines match their frozen SHA-256 hashes on disk.
2. Historical 60 runtime closure files match their frozen SHA-256 hashes at git commit 17b5539c.
3. Frozen QSC2-SO1 runner, protocol, live marker, and live trace identities remain 100% invariant.
4. Current worktree drift is strictly audited: exactly 1 file (src/pesu_agent/search/webview_slack_search.py)
   has drifted, corresponding strictly to the SFC2 pre-submit safety remediation.
5. Historical runner fails closed on the unmitigated worktree with RUNTIME_IMPORT_CLOSURE_DRIFT.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FROZEN_COMMIT = "17b5539c4991e782535fb10c105421141e5a97fc"
PROTO_FILE = REPO_ROOT / "artifacts" / "task11q4_search_qsc2_so1_protocol_freeze.json"


def get_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def test_historical_34_baselines_integrity():
    """Contract 1: All 34 historical baseline files on disk match their frozen SHA-256."""
    assert PROTO_FILE.exists(), f"Missing protocol freeze manifest: {PROTO_FILE}"
    proto = json.loads(PROTO_FILE.read_text(encoding="utf-8"))
    baselines = proto.get("historical_baselines", {})
    assert len(baselines) == 34, f"Expected 34 baselines, found {len(baselines)}"

    for name, base_info in baselines.items():
        rel_path = base_info.get("path") if isinstance(base_info, dict) else name
        exp_sha = base_info.get("sha256") if isinstance(base_info, dict) else base_info
        fp = REPO_ROOT / rel_path
        assert fp.exists(), f"Baseline file missing: {rel_path} ({name})"
        act_sha = get_file_sha256(fp)
        assert act_sha == exp_sha, f"Baseline SHA mismatch for {rel_path}: exp {exp_sha}, got {act_sha}"


def test_historical_git_snapshot_60_closure_files_integrity():
    """Contract 2: All 60 runtime closure files in git snapshot 17b5539c match their frozen SHA-256 with 0 drift."""
    proto = json.loads(PROTO_FILE.read_text(encoding="utf-8"))
    closure = proto.get("runtime_closure_hashes", {})
    assert len(closure) == 60, f"Expected 60 runtime closure files, found {len(closure)}"

    mismatches = []
    for rel_path, exp_sha in closure.items():
        try:
            git_content = subprocess.check_output(
                ["git", "show", f"{FROZEN_COMMIT}:{rel_path}"],
                stderr=subprocess.DEVNULL
            )
            sha_raw = hashlib.sha256(git_content).hexdigest()
            sha_crlf = hashlib.sha256(git_content.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')).hexdigest()
            if exp_sha != sha_raw and exp_sha != sha_crlf:
                mismatches.append(f"{rel_path}: expected {exp_sha}, got raw={sha_raw}, crlf={sha_crlf}")
        except Exception as ex:
            mismatches.append(f"git show failed for {rel_path}: {ex}")

    assert len(mismatches) == 0, f"Snapshot closure verification failed: {mismatches}"


def test_frozen_qsc2_so1_identities_integrity():
    """Contract 3: QSC2-SO1 Runner, Protocol, Live Marker, and Live Trace SHAs remain strictly invariant."""
    runner_path = REPO_ROOT / "scripts" / "diagnose_task11q4_search_safe_suggestion_availability_qsc2_so1.py"
    marker_path = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_search_qsc2_so1_attempt_started.json"
    live_path = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_search_qsc2_so1_query_state_convergence.json"

    assert get_file_sha256(runner_path) == "c8d5b038ea1daa35d34a9445283598d8d21707bc68dd5874ccec90b9986be993"
    assert get_file_sha256(PROTO_FILE) == "46b9078a2a5d89af04f41ca901484062832e211f8b0b67afa63a507c02dfede2"
    assert get_file_sha256(marker_path) == "3d1b6a8dcb9660c07261f5f3c30670697386aecaebaf6819fe7a33c34484b640"
    assert get_file_sha256(live_path) == "c61887b245af8711c8306e54bf456099f3ed0849424435d40b041334ffe4cdfb"


def test_current_worktree_drift_audit():
    """Contract 4: Current worktree audit proves that ONLY webview_slack_search.py differs from snapshot."""
    import scripts.diagnose_task11q4_search_safe_suggestion_availability_qsc2_so1 as runner
    proto = json.loads(PROTO_FILE.read_text(encoding="utf-8"))
    closure = proto.get("runtime_closure_hashes", {})

    closure_ok, closure_errors = runner.audit_runtime_import_closure(closure)
    assert closure_ok is False, "Worktree should not report clean match against frozen snapshot"
    assert len(closure_errors) == 1, f"Expected exactly 1 drifted file, found: {closure_errors}"
    assert "src/pesu_agent/search/webview_slack_search.py" in closure_errors[0]


def test_unmitigated_historical_runner_fails_closed_on_drifted_worktree(tmp_path):
    """Contract 5: Historical runner executed directly on drifted worktree strictly fails closed with RUNTIME_IMPORT_CLOSURE_DRIFT."""
    import scripts.diagnose_task11q4_search_safe_suggestion_availability_qsc2_so1 as runner
    from tests.test_task11q4_search_qsc2_so1_read_only_observer import MockLiveWebViewHostQSC2, MockLiveBackendQSC2

    marker_p = tmp_path / "marker_drift.json"
    output_p = tmp_path / "live_drift.json"
    host = MockLiveWebViewHostQSC2()

    report = runner.run_qsc2_so1_qualification(
        dry_run=False,
        timeout=1.0,
        marker_file_override=marker_p,
        output_file_override=output_p,
        host_factory=lambda **kw: host,
        backend_factory=lambda **kw: MockLiveBackendQSC2(),
    )

    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "RUNTIME_IMPORT_CLOSURE_DRIFT"
    assert "src/pesu_agent/search/webview_slack_search.py" in str(report.get("error", ""))
