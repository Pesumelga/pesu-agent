"""Tests for Phase 13.5D / Task 11 / P2A / MDR1 Mounted DOM Residency Diagnostic.

Strict qualification suite verifying:
- Backend max_results remains 5 (no production budget expansion, P1B closed)
- Production row selector parity
- Mounted DOM inspection cap == 20
- No top-5 slice in diagnostic snapshot, but no production selection modification
- Target found at mounted position 1, 5, 6, 20
- Target absent
- Target duplicated
- DOM position not labeled production rank
- Connected offscreen row is still inspectable for residency
- Positive geometry tracked separately from connection
- Raw href never persisted, message/sender text never persisted
- Current-query correlation proven case
- Current-query correlation unproven case
- Stale/recycled-row possibility fails promotion
- Zero click, zero scroll, zero keyboard, zero navigation
- One-shot marker before WebView, replay blocked by marker, replay blocked by artifact
- Handled failure persists artifact
- VRA1 immutable marker and live artifact exact hashes
- Historical baseline hashes exact
- Runtime closure exact set
- Production src/ diff empty
- Dry run passes with zero live actions
- Deterministic Node/DOM fixture execution for row parsing
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1 as mdr1


class TestMdr1ParityAndStaticContracts:
    def test_production_row_enumeration_parity(self):
        assert mdr1.verify_search_result_row_enumeration_parity() is True

    def test_container_and_row_selectors_match_production(self):
        assert mdr1.SEARCH_VIEW_SELECTOR == mdr1.PROD_SEARCH_VIEW_SELECTOR
        assert mdr1.SEARCH_RESULT_ROW_SELECTORS == mdr1.PROD_SEARCH_RESULT_ROW_SELECTORS

    def test_runtime_search_path_parity(self):
        assert mdr1.verify_runtime_search_path_parity_with_attempt4() is True

    def test_search_telemetry_authority_parity(self):
        assert mdr1.verify_search_telemetry_authority_parity_with_attempt4() is True

    def test_search_exception_order_parity(self):
        assert mdr1.verify_search_exception_order_parity_with_attempt4() is True

    def test_search_safety_gate_parity(self):
        assert mdr1.verify_search_safety_gate_parity_with_attempt4() is True

    def test_host_dom_evaluation_api_parity(self):
        assert mdr1.verify_host_dom_evaluation_api_parity() is True

    def test_static_zero_interaction_audit(self):
        assert mdr1.verify_static_zero_interaction_contract() is True

    def test_no_executable_click_in_js(self):
        js = mdr1.JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT
        assert not re.search(r"\.click\s*\(", js)
        assert not re.search(r"\bMouseEvent\b", js)
        assert not re.search(r"\bInputEvent\b", js)

    def test_no_executable_scroll_in_js(self):
        js = mdr1.JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT
        assert not re.search(r"\bscrollIntoView\b", js)
        assert not re.search(r"\bscrollTo\b", js)
        assert not re.search(r"\bscrollBy\b", js)
        assert not re.search(r"\bscrollTop\s*=", js)
        assert not re.search(r"\bscrollLeft\s*=", js)
        assert not re.search(r"\bwheel\b", js)

    def test_no_keyboard_or_navigation_in_js(self):
        js = mdr1.JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT
        assert not re.search(r"\bKeyboardEvent\b", js)
        assert not re.search(r"\bhistory\.push", js)
        assert not re.search(r"\bhistory\.replace", js)
        assert not re.search(r"\blocation\s*=", js)
        assert not re.search(r"\blocation\.href\s*=", js)
        assert not re.search(r"\.submit\s*\(", js)

    def test_backend_search_max_results_remains_5(self):
        assert mdr1.SEARCH_MAX_RESULTS == 5

    def test_max_mounted_rows_cap_is_20(self):
        assert mdr1.MAX_MOUNTED_ROWS_TO_INSPECT == 20

    def test_diagnostic_snapshot_does_not_slice_to_top5(self):
        js = mdr1.JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT
        assert "slice(0, 5)" not in js
        assert "slice(0, maxMountedRows)" in js

    def test_action_contracts_zero_budgets(self):
        with open(mdr1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        ac = manifest["action_contracts"]
        assert ac.get("MAX_GUARDED_ANCHOR_CLICKS") == 0
        assert ac.get("MAX_REPLY_BAR_CLICKS") == 0
        assert ac.get("MAX_RESULT_CARD_CLICKS") == 0
        assert ac.get("MAX_THREAD_EXPANSIONS") == 0
        assert ac.get("MAX_SCROLL_ACTIONS") == 0
        assert ac.get("MAX_KEYBOARD_EVENTS") == 0
        assert ac.get("MAX_COMPOSER_INTERACTIONS") == 0
        assert ac.get("MAX_WRITE_CAPABLE_ACTIONS") == 0
        assert ac.get("MAX_PROVIDER_CALLS") == 0
        assert ac.get("MAX_RETRIES") == 0
        assert ac.get("MAX_RESTORATIONS") == 0
        assert ac.get("MAX_BACK_NAVIGATIONS") == 0
        assert ac.get("MAX_READ_ONLY_DOM_SNAPSHOTS") == 1
        assert ac.get("MAX_SLACK_SEARCH_OPERATIONS") == 1
        assert ac.get("MAX_SEARCH_SUBMISSIONS") == 1


class TestMdr1HistoricalAndGovernance:
    def test_vra1_marker_sha256_exact(self):
        marker_p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_vra1_attempt_started.json"
        assert marker_p.exists()
        actual_sha = mdr1.get_file_sha256(marker_p)
        assert actual_sha == "cc19a73e2d2da7978218982443147cb890e504d851d945dd4087451195466c59"

    def test_vra1_live_artifact_sha256_exact(self):
        artifact_p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_vra1_viewport_reachability.json"
        assert artifact_p.exists()
        actual_sha = mdr1.get_file_sha256(artifact_p)
        assert actual_sha == "ae252ecdac609e366fc914a9f6a8645a5b3a108d005bc1600639bc54b48a1032"

    def test_taiq1_baselines_exact(self):
        marker_p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_taiq1_attempt_started.json"
        live_p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_taiq1_anchor_interaction.json"
        assert marker_p.exists() and live_p.exists()
        assert mdr1.get_file_sha256(marker_p) == "48f6709bea9b8718fa5f9bf8dbebe5d5290d395e6d9eb59556a56a48ffc64c4e"
        assert mdr1.get_file_sha256(live_p) == "a59b7c5b2aa04fd137efea688d563a8c9a26afc03b770d6683b46ec4bbfc9037"

    def test_all_16_historical_baselines_exact(self):
        with open(mdr1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        hb = manifest["historical_baselines"]
        assert len(hb) == 16
        for name, info in hb.items():
            p = REPO_ROOT / info["path"]
            assert p.exists(), f"Historical baseline file missing: {p}"
            actual_sha = mdr1.get_file_sha256(p)
            assert actual_sha == info["sha256"], f"SHA256 mismatch for {name}: expected {info['sha256']}, got {actual_sha}"

    def test_marker_and_live_artifact_absent_during_prep(self):
        assert not mdr1.ATTEMPT_STARTED_MARKER_FILE.exists(), "MDR1 attempt marker must be absent during prep"
        assert not mdr1.LIVE_OUTPUT_FILE.exists(), "MDR1 live artifact must be absent during prep"

    def test_pre_existing_marker_blocks_replay(self, tmp_path, monkeypatch):
        fake_marker = tmp_path / "fake_marker.json"
        fake_marker.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mdr1, "ATTEMPT_STARTED_MARKER_FILE", fake_marker)
        monkeypatch.setattr(mdr1, "LIVE_OUTPUT_FILE", tmp_path / "absent_live.json")
        res = mdr1.run_mdr1_diagnostic(dry_run=False)
        assert res["verdict"] == "FAIL_CLOSED"
        assert res["diagnostic_verdict"] == "ATTEMPT_ALREADY_CONSUMED"

    def test_pre_existing_live_artifact_blocks_replay(self, tmp_path, monkeypatch):
        fake_live = tmp_path / "fake_live.json"
        fake_live.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mdr1, "LIVE_OUTPUT_FILE", fake_live)
        monkeypatch.setattr(mdr1, "ATTEMPT_STARTED_MARKER_FILE", tmp_path / "absent_marker.json")
        res = mdr1.run_mdr1_diagnostic(dry_run=False)
        assert res["verdict"] == "FAIL_CLOSED"
        assert res["diagnostic_verdict"] == "LIVE_ARTIFACT_ALREADY_EXISTS"

    def test_runtime_closure_exact_set(self):
        with open(mdr1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        audit = mdr1.audit_runtime_import_closure(manifest)
        assert audit["closure_audit_passed"] is True
        assert audit["missing_from_runtime_closure"] == []
        assert audit["stale_runtime_closure_entries"] == []
        assert audit["mismatched_runtime_dependencies"] == []

    def test_sensitive_data_contract_all_false(self):
        with open(mdr1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        sc = manifest["sensitive_content_contract"]
        for k, v in sc.items():
            assert v is False, f"Sensitive contract key {k} must be False"

    def test_dry_run_produces_dry_run_pass(self):
        res = mdr1.run_mdr1_diagnostic(dry_run=True)
        assert res["verdict"] == "DRY_RUN_PASS"
        assert res["diagnostic_verdict"] == "DRY_RUN_PASS"
        inv = res["live_action_inventory"]
        assert inv["search_submissions"] == 0
        assert inv["read_only_dom_snapshots"] == 0
        assert inv["guarded_anchor_clicks"] == 0
        assert inv["scroll_actions"] == 0
        assert res["provider_calls"] == 0
        assert res["simulation_dom"] is not None
        assert res["dom_telemetry"] is None

    def test_production_src_diff_empty(self):
        res = subprocess.run(
            ["git", "diff", "HEAD", "--", "src/"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert res.stdout.strip() == "", f"Production src/ diff must be empty: {res.stdout}"


class TestMdr1PythonClassifier:
    def test_current_query_correlation_proven(self):
        auth = mdr1.evaluate_current_query_correlation_authority(
            search_view_connected=True,
            surface_query_matches=True,
            search_readiness="RESULTS_READY",
            mounted_row_count=20,
        )
        assert auth == "CURRENT_SEARCH_SURFACE_CORRELATED"

    def test_current_query_correlation_unproven_query_mismatch(self):
        auth = mdr1.evaluate_current_query_correlation_authority(
            search_view_connected=True,
            surface_query_matches=False,
            search_readiness="RESULTS_READY",
            mounted_row_count=20,
        )
        assert auth == "MOUNTED_BUT_CURRENT_QUERY_CORRELATION_UNPROVEN"

    def test_current_query_correlation_unproven_not_ready(self):
        auth = mdr1.evaluate_current_query_correlation_authority(
            search_view_connected=True,
            surface_query_matches=True,
            search_readiness="LOADING",
            mounted_row_count=20,
        )
        assert auth == "MOUNTED_BUT_CURRENT_QUERY_CORRELATION_UNPROVEN"

    def test_stale_or_recycled_row_possible_disconnected_view(self):
        auth = mdr1.evaluate_current_query_correlation_authority(
            search_view_connected=False,
            surface_query_matches=True,
            search_readiness="RESULTS_READY",
            mounted_row_count=20,
        )
        assert auth == "STALE_OR_RECYCLED_ROW_POSSIBLE"

    def test_stale_or_recycled_row_possible_zero_mounted(self):
        auth = mdr1.evaluate_current_query_correlation_authority(
            search_view_connected=True,
            surface_query_matches=True,
            search_readiness="RESULTS_READY",
            mounted_row_count=0,
        )
        assert auth == "STALE_OR_RECYCLED_ROW_POSSIBLE"

    def test_target_found_current_query_correlated(self):
        dom = {"ok": True, "search_view_connected": True, "target_match_count": 1, "mounted_row_count": 20}
        cls = mdr1.classify_mounted_dom_residency(dom, "CURRENT_SEARCH_SURFACE_CORRELATED")
        assert cls == "TARGET_FOUND_IN_MOUNTED_DOM_CURRENT_QUERY_CORRELATED"

    def test_target_found_query_correlation_unproven(self):
        dom = {"ok": True, "search_view_connected": True, "target_match_count": 1, "mounted_row_count": 20}
        cls = mdr1.classify_mounted_dom_residency(dom, "MOUNTED_BUT_CURRENT_QUERY_CORRELATION_UNPROVEN")
        assert cls == "TARGET_FOUND_IN_MOUNTED_DOM_QUERY_CORRELATION_UNPROVEN"

    def test_target_not_found_in_mounted_dom(self):
        dom = {"ok": True, "search_view_connected": True, "target_match_count": 0, "mounted_row_count": 20}
        cls = mdr1.classify_mounted_dom_residency(dom, "CURRENT_SEARCH_SURFACE_CORRELATED")
        assert cls == "TARGET_NOT_FOUND_IN_MOUNTED_DOM"

    def test_target_identity_duplicated_in_mounted_dom(self):
        dom = {"ok": True, "search_view_connected": True, "target_match_count": 2, "mounted_row_count": 20}
        cls = mdr1.classify_mounted_dom_residency(dom, "CURRENT_SEARCH_SURFACE_CORRELATED")
        assert cls == "TARGET_IDENTITY_DUPLICATED_IN_MOUNTED_DOM"

    def test_mounted_dom_state_insufficient(self):
        dom = {"ok": True, "search_view_connected": True, "target_match_count": 0, "mounted_row_count": 0}
        cls = mdr1.classify_mounted_dom_residency(dom, "STALE_OR_RECYCLED_ROW_POSSIBLE")
        assert cls == "MOUNTED_DOM_STATE_INSUFFICIENT"

    def test_fail_closed_on_snapshot_failure(self):
        dom = {"ok": False, "failure_reason": "SEARCH_VIEW_NOT_FOUND", "search_view_connected": False}
        cls = mdr1.classify_mounted_dom_residency(dom, "STALE_OR_RECYCLED_ROW_POSSIBLE")
        assert cls == "FAIL_CLOSED"


class TestMdr1NodeDOMInspection:
    """Executes JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT in Node.js with deterministic DOM fixtures."""

    def _execute_snapshot_in_node(self, fixture_cfg: dict[str, Any]) -> dict[str, Any]:
        cfg_json = json.dumps(fixture_cfg)
        node_script = f"""
const JS_SNAPSHOT = {mdr1.JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT};
const cfg = {cfg_json};

function runFixture() {{
    global.window = {{
        location: {{ href: "https://app.slack.com/client/T042WS00000/C042WS25QAX" }}
    }};
    global.URL = require("url").URL;

    if (cfg.searchViewNotFound) {{
        global.document = {{
            querySelector: () => null
        }};
        return JS_SNAPSHOT(
            {json.dumps(mdr1.SEARCH_VIEW_SELECTOR)},
            {json.dumps(mdr1.SEARCH_RESULT_ROW_SELECTORS)},
            {mdr1.MAX_MOUNTED_ROWS_TO_INSPECT},
            "{mdr1.TARGET_CHANNEL_ID}",
            "{mdr1.TARGET_MESSAGE_TS}",
            "{mdr1.TARGET_THREAD_TS}",
            {json.dumps(mdr1.FROZEN_ALLOWED_ORIGINS)}
        );
    }}

    const rowConfigs = cfg.rows || [];
    const mockRows = rowConfigs.map((rc, idx) => {{
        const isConn = rc.connected !== false;
        const width = rc.positiveGeometry !== false ? (rc.width || 400) : 0;
        const height = rc.positiveGeometry !== false ? (rc.height || 50) : 0;

        const mockAnchors = (rc.anchors || []).map(ac => ({{
            isConnected: isConn,
            href: ac.href
        }}));

        return {{
            isConnected: isConn,
            getBoundingClientRect: () => ({{
                left: 100,
                top: idx * 60,
                right: 100 + width,
                bottom: idx * 60 + height,
                width: width,
                height: height
            }}),
            querySelectorAll: (sel) => {{
                if (sel === "a[href]") return mockAnchors;
                return [];
            }}
        }};
    }});

    const mockSearchView = {{
        isConnected: true,
        querySelectorAll: (sel) => {{
            if (sel.includes("data-qa=\\"search_result\\"") || sel.includes(".c-search_result")) {{
                return mockRows;
            }}
            return [];
        }}
    }};

    global.document = {{
        querySelector: (sel) => {{
            if (sel === {json.dumps(mdr1.SEARCH_VIEW_SELECTOR)}) return mockSearchView;
            return null;
        }}
    }};

    return JS_SNAPSHOT(
        {json.dumps(mdr1.SEARCH_VIEW_SELECTOR)},
        {json.dumps(mdr1.SEARCH_RESULT_ROW_SELECTORS)},
        {mdr1.MAX_MOUNTED_ROWS_TO_INSPECT},
        "{mdr1.TARGET_CHANNEL_ID}",
        "{mdr1.TARGET_MESSAGE_TS}",
        "{mdr1.TARGET_THREAD_TS}",
        {json.dumps(mdr1.FROZEN_ALLOWED_ORIGINS)}
    );
}}

console.log(JSON.stringify(runFixture()));
"""
        proc = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout)

    def _build_standard_20_rows(self, target_position: int | None = None) -> list[dict[str, Any]]:
        rows = []
        for i in range(1, 21):
            is_target = (i == target_position)
            if is_target:
                permalink = f"https://heumlabs.slack.com/archives/{mdr1.TARGET_CHANNEL_ID}/p1718079276126469?thread_ts={mdr1.TARGET_THREAD_TS}"
            else:
                permalink = f"https://heumlabs.slack.com/archives/C0000000000/p1788000000{i:06d}?thread_ts=1788000000.{i:06d}"
            rows.append({
                "connected": True,
                "positiveGeometry": True,
                "anchors": [{"href": permalink}]
            })
        return rows

    def test_target_found_at_position_1(self):
        rows = self._build_standard_20_rows(target_position=1)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["mounted_row_count"] == 20
        assert res["inspected_mounted_row_count"] == 20
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 1
        assert res["target_connected"] is True
        assert res["target_positive_geometry"] is True

    def test_target_found_at_position_5(self):
        rows = self._build_standard_20_rows(target_position=5)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 5

    def test_target_found_at_position_6(self):
        rows = self._build_standard_20_rows(target_position=6)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 6

    def test_target_found_at_position_20(self):
        rows = self._build_standard_20_rows(target_position=20)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 20

    def test_target_absent(self):
        rows = self._build_standard_20_rows(target_position=None)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 0
        assert res["target_mounted_dom_position"] is None

    def test_target_duplicated(self):
        rows = self._build_standard_20_rows(target_position=3)
        # Duplicate at position 7
        target_link = f"https://heumlabs.slack.com/archives/{mdr1.TARGET_CHANNEL_ID}/p1718079276126469?thread_ts={mdr1.TARGET_THREAD_TS}"
        rows[6]["anchors"] = [{"href": target_link}]
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 2
        assert res["target_mounted_dom_position"] is None

    def test_dom_position_not_labeled_production_rank(self):
        rows = self._build_standard_20_rows(target_position=6)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert "target_mounted_dom_position" in res
        assert "target_rank" not in res
        assert "production_rank" not in res
        assert "search_rank" not in res

    def test_connected_offscreen_row_still_inspectable_for_residency(self):
        rows = self._build_standard_20_rows(target_position=12)
        # Row 12 has positive geometry, top: 12*60 = 720 (could be offscreen in small viewport)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 12
        assert res["target_connected"] is True

    def test_positive_geometry_tracked_separately_from_connection(self):
        rows = self._build_standard_20_rows(target_position=8)
        # Row 8 is connected, but has width=0, height=0
        rows[7]["positiveGeometry"] = False
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 8
        assert res["target_connected"] is True
        assert res["target_positive_geometry"] is False
        assert res["connected_row_count"] == 20
        assert res["positive_geometry_row_count"] == 19

    def test_raw_href_and_text_never_persisted(self):
        rows = self._build_standard_20_rows(target_position=5)
        res = self._execute_snapshot_in_node({"rows": rows})
        res_str = json.dumps(res)
        assert "https://" not in res_str
        assert "archives" not in res_str
        assert "innerHTML" not in res_str
        assert "textContent" not in res_str
        for r in res["mounted_row_records"]:
            assert "href" not in r
            assert "text" not in r
            assert "sender" not in r

    def test_search_view_not_found_handled(self):
        res = self._execute_snapshot_in_node({"searchViewNotFound": True})
        assert res["ok"] is False
        assert res["failure_reason"] == "SEARCH_VIEW_NOT_FOUND"
        assert res["search_view_connected"] is False
