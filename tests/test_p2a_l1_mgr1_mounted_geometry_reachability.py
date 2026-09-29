"""Tests for Phase 13.5D / Task 11 / P2A / MGR1: Mounted Target Geometry Reachability Diagnostic 1."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import diagnose_task11q4_p2a_l1_mounted_geometry_reachability_mgr1 as mgr1


# ===========================================================================
# 1. Parity & Static Contracts
# ===========================================================================

class TestMgr1ParityAndStaticContracts:
    """Verifies that MGR1 adheres to Attempt 4 parity and zero-interaction rules."""

    def test_search_telemetry_authority_parity(self):
        assert mgr1.verify_search_telemetry_authority_parity_with_attempt4() is True

    def test_search_exception_order_parity(self):
        assert mgr1.verify_search_exception_order_parity_with_attempt4() is True

    def test_search_safety_gate_parity(self):
        assert mgr1.verify_search_safety_gate_parity_with_attempt4() is True

    def test_runtime_search_path_parity(self):
        assert mgr1.verify_runtime_search_path_parity_with_attempt4() is True

    def test_host_dom_evaluation_api_parity(self):
        assert mgr1.verify_host_dom_evaluation_api_parity() is True

    def test_search_result_row_enumeration_parity(self):
        assert mgr1.verify_search_result_row_enumeration_parity() is True

    def test_static_zero_interaction_contract(self):
        assert mgr1.verify_static_zero_interaction_contract() is True

    def test_strict_action_contract_limits(self):
        contracts = {
            "MAX_SLACK_SEARCH_OPERATIONS": mgr1.MAX_SLACK_SEARCH_OPERATIONS,
            "MAX_SEARCH_SUBMISSIONS": mgr1.MAX_SEARCH_SUBMISSIONS,
            "MAX_READ_ONLY_DOM_SNAPSHOTS": mgr1.MAX_READ_ONLY_DOM_SNAPSHOTS,
            "MAX_GUARDED_ANCHOR_CLICKS": mgr1.MAX_GUARDED_ANCHOR_CLICKS,
            "MAX_REPLY_BAR_CLICKS": mgr1.MAX_REPLY_BAR_CLICKS,
            "MAX_RESULT_CARD_CLICKS": mgr1.MAX_RESULT_CARD_CLICKS,
            "MAX_THREAD_EXPANSIONS": mgr1.MAX_THREAD_EXPANSIONS,
            "MAX_SCROLL_ACTIONS": mgr1.MAX_SCROLL_ACTIONS,
            "MAX_KEYBOARD_EVENTS": mgr1.MAX_KEYBOARD_EVENTS,
            "MAX_COMPOSER_INTERACTIONS": mgr1.MAX_COMPOSER_INTERACTIONS,
            "MAX_WRITE_CAPABLE_ACTIONS": mgr1.MAX_WRITE_CAPABLE_ACTIONS,
            "MAX_PROVIDER_CALLS": mgr1.MAX_PROVIDER_CALLS,
            "MAX_RETRIES": mgr1.MAX_RETRIES,
            "MAX_RESTORATIONS": mgr1.MAX_RESTORATIONS,
            "MAX_BACK_NAVIGATIONS": mgr1.MAX_BACK_NAVIGATIONS,
        }
        assert contracts["MAX_SLACK_SEARCH_OPERATIONS"] == 1
        assert contracts["MAX_SEARCH_SUBMISSIONS"] == 1
        assert contracts["MAX_READ_ONLY_DOM_SNAPSHOTS"] == 1
        for key, val in contracts.items():
            if key not in ("MAX_SLACK_SEARCH_OPERATIONS", "MAX_SEARCH_SUBMISSIONS", "MAX_READ_ONLY_DOM_SNAPSHOTS"):
                assert val == 0, f"Contract {key} must be strictly 0, got {val}"

    def test_scroll_top_only_read_never_written(self):
        js = mgr1.JS_ATOMIC_READ_ONLY_MOUNTED_TARGET_GEOMETRY_SNAPSHOT
        assert not re.search(r"\bscrollTop\s*=", js)
        assert not re.search(r"\bscrollLeft\s*=", js)
        assert re.search(r"\bscrollContainerEl\.scrollTop\b", js)
        assert re.search(r"\bscrollContainerEl\.scrollLeft\b", js)


# ===========================================================================
# 2. Historical Baselines & Governance
# ===========================================================================

class TestMgr1HistoricalAndGovernance:
    """Verifies all 20 historical baselines, runtime closure, and one-shot gates."""

    def test_protocol_freeze_exists_and_valid(self):
        assert mgr1.PROTOCOL_FREEZE_PATH.exists()
        proto = json.loads(mgr1.PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))
        assert proto["protocol"] == mgr1.PROTOCOL_NAME
        assert proto["diagnostic_run"] == mgr1.DIAGNOSTIC_RUN
        assert proto["search_max_results"] == 5
        assert proto["max_mounted_rows_to_inspect"] == 20
        assert proto["canonical_dry_run_verdict"] == "DRY_RUN_PASS"

    def test_all_20_historical_baselines_exact(self):
        proto = json.loads(mgr1.PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))
        baselines = proto.get("historical_baselines", {})
        assert len(baselines) == 20

        for name, info in baselines.items():
            p = REPO_ROOT / info["path"]
            assert p.exists(), f"Historical baseline file {name} missing: {p}"
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            assert h == info["sha256"], f"Baseline hash mismatch for {name}: expected {info['sha256']}, got {h}"

    def test_mdr1r1_frozen_marker_and_live_hashes(self):
        proto = json.loads(mgr1.PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))
        baselines = proto["historical_baselines"]
        assert baselines["mdr1r1_marker"]["sha256"] == "678cc3c76908b49e70cc6d85548bc51e48e0e7e4d7831e1fe10c270ebc75909c"
        assert baselines["mdr1r1_live"]["sha256"] == "36d15d0657baa37f6d1bdd4817e1dff544dfb54d5fe654a60518b173d590e54d"

    def test_production_src_diff_empty(self):
        res = subprocess.run(
            ["git", "diff", "HEAD", "--", "src/"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert res.stdout.strip() == "", "Production src/ must have zero diff against HEAD"

    def test_marker_and_live_artifact_absent_during_prep(self):
        assert not mgr1.ATTEMPT_STARTED_MARKER_FILE.exists(), "MGR1 attempt marker must NOT exist during prep"
        assert not mgr1.LIVE_OUTPUT_FILE.exists(), "MGR1 live artifact must NOT exist during prep"

    def test_pre_existing_marker_blocks_replay(self, tmp_path, monkeypatch):
        fake_marker = tmp_path / "task11q4_p2a_l1_mgr1_attempt_started.json"
        fake_marker.write_text("{}", encoding="utf-8")
        fake_live = tmp_path / "task11q4_p2a_l1_mgr1_mounted_geometry_reachability.json"

        monkeypatch.setattr(mgr1, "ATTEMPT_STARTED_MARKER_FILE", fake_marker)
        monkeypatch.setattr(mgr1, "LIVE_OUTPUT_FILE", fake_live)

        rep = mgr1.run_mgr1_diagnostic(dry_run=False)
        assert rep["verdict"] == "FAIL_CLOSED"
        assert rep["diagnostic_verdict"] == "ATTEMPT_ALREADY_CONSUMED"

    def test_pre_existing_live_artifact_blocks_replay(self, tmp_path, monkeypatch):
        fake_marker = tmp_path / "task11q4_p2a_l1_mgr1_attempt_started.json"
        fake_live = tmp_path / "task11q4_p2a_l1_mgr1_mounted_geometry_reachability.json"
        fake_live.write_text("{}", encoding="utf-8")

        monkeypatch.setattr(mgr1, "ATTEMPT_STARTED_MARKER_FILE", fake_marker)
        monkeypatch.setattr(mgr1, "LIVE_OUTPUT_FILE", fake_live)

        rep = mgr1.run_mgr1_diagnostic(dry_run=False)
        assert rep["verdict"] == "FAIL_CLOSED"
        assert rep["diagnostic_verdict"] == "LIVE_ARTIFACT_ALREADY_EXISTS"

    def test_runtime_closure_exactness(self):
        proto = json.loads(mgr1.PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))
        res = mgr1.audit_runtime_import_closure(proto)
        assert res["closure_audit_passed"] is True
        assert len(res["missing_from_closure"]) == 0
        assert len(res["stale_in_closure"]) == 0
        assert len(res["mismatched_hashes"]) == 0
        assert res["loaded_file_count"] == 60


# ===========================================================================
# 3. Canonical Identity & Membership Authorities
# ===========================================================================

class TestMgr1CanonicalIdentityAndMembership:
    """Verifies target selection by exact semantic identity, separate correlation, and row membership."""

    def test_target_identity_constants(self):
        assert mgr1.TARGET_CHANNEL_ID == "C042WS25QAX"
        assert mgr1.TARGET_MESSAGE_TS == "1718079276.126469"
        assert mgr1.TARGET_THREAD_TS == "1718058683.912369"
        assert mgr1.TARGET_IS_THREAD_REPLY is True

    def test_build_canonical_backend_identity_set(self):
        mock_raw = mgr1.build_mock_dry_run_backend_results()
        ident_set, stats = mgr1.build_canonical_backend_identity_set(mock_raw)
        assert stats["backend_result_count"] == 5
        assert stats["backend_identity_parse_success_count"] == 5
        assert stats["backend_identity_parse_failure_count"] == 0
        assert len(ident_set) == 5
        assert mgr1.CANONICAL_TARGET_IDENTITY not in ident_set

    def test_row_membership_proven_vs_unproven(self):
        mock_raw = mgr1.build_mock_dry_run_backend_results()
        ident_set, _ = mgr1.build_canonical_backend_identity_set(mock_raw)

        proven_ident = ("D07RA9YDE8G", "1788483883.410459", "1788483883.410459")
        mem1 = mgr1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=proven_ident,
            backend_identity_set=ident_set,
        )
        assert mem1 == "PROVEN"

        mem2 = mgr1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=mgr1.CANONICAL_TARGET_IDENTITY,
            backend_identity_set=ident_set,
        )
        assert mem2 == "UNPROVEN"

        mem3 = mgr1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=False,
            row_identity=proven_ident,
            backend_identity_set=ident_set,
        )
        assert mem3 == "STALE_OR_RECYCLED_POSSIBLE"

    def test_search_surface_correlation_semantics(self):
        assert mgr1.evaluate_search_surface_current_query_correlation(True, True, "RESULTS_READY") is True
        assert mgr1.evaluate_search_surface_current_query_correlation(False, True, "RESULTS_READY") is False
        assert mgr1.evaluate_search_surface_current_query_correlation(True, False, "RESULTS_READY") is False
        assert mgr1.evaluate_search_surface_current_query_correlation(True, True, "LOADING") is False


# ===========================================================================
# 4. Deterministic Geometry Classifications (Python Authority)
# ===========================================================================

class TestMgr1DeterministicGeometryClassifications:
    """Verifies all diagnostic outcomes and geometric boundaries in the Python classification hierarchy."""

    def test_classification_target_absent(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="target_absent")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_NOT_FOUND_IN_CONNECTED_MOUNTED_DOM"

    def test_classification_target_duplicate(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="target_duplicate")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_IDENTITY_DUPLICATED_IN_CONNECTED_MOUNTED_DOM"

    def test_classification_anchor_absent(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="anchor_absent")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ROW_FOUND_ANCHOR_NOT_FOUND"

    def test_classification_anchor_duplicate(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="anchor_duplicate")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ROW_FOUND_ANCHOR_NOT_UNIQUE"

    def test_classification_anchor_geometry_invalid(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="below_viewport")
        snap["target_anchor_geometry"]["anchor_positive_geometry"] = False
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_GEOMETRY_INVALID"

    def test_classification_below_layout_viewport(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="below_viewport")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT"

    def test_classification_above_layout_viewport(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="above_viewport")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_ABOVE_LAYOUT_VIEWPORT"

    def test_classification_left_of_layout_viewport(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="left_of_viewport")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_LEFT_OF_LAYOUT_VIEWPORT"

    def test_classification_right_of_layout_viewport(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="right_of_viewport")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_RIGHT_OF_LAYOUT_VIEWPORT"

    def test_classification_outside_scroll_viewport(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="outside_scroll_viewport")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_OUTSIDE_SCROLL_CONTAINER_VIEWPORT"

    def test_classification_clipped_by_ancestor(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="clipped_by_ancestor")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_CLIPPED_BY_ANCESTOR"

    def test_classification_element_from_point_null(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="hit_test_null")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_CENTER_IN_VIEWPORT_ELEMENT_FROM_POINT_NULL"

    def test_classification_occluded_within_viewport(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="inside_both_viewports")
        snap["hit_test"]["element_from_point_evaluated"] = True
        snap["hit_test"]["element_from_point_is_null"] = False
        snap["hit_test"]["anchor_contains_element_from_point"] = False
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_OCCLUDED_WITHIN_VIEWPORT"

    def test_classification_visible_and_reachable_anchor(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="hit_test_anchor")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"

    def test_classification_visible_and_reachable_descendant(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="hit_test_descendant")
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"

    @pytest.mark.parametrize("pos", [1, 5, 6, 19, 20])
    def test_target_found_at_various_mounted_positions(self, pos):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=pos, case="visible_reachable")
        assert snap["target_match_count"] == 1
        assert snap["target_mounted_dom_position"] == pos
        assert mgr1.classify_mounted_geometry_reachability(snap) == "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"

    def test_positive_geometry_does_not_imply_visibility(self):
        snap = mgr1.build_mock_dry_run_dom_snapshot(simulated_position=19, case="below_viewport")
        assert snap["target_anchor_geometry"]["anchor_positive_geometry"] is True
        classification = mgr1.classify_mounted_geometry_reachability(snap)
        assert classification == "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT"
        assert classification != "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"


# ===========================================================================
# 5. Node.js DOM Inspection Fixture Execution
# ===========================================================================

class TestMgr1NodeDOMInspection:
    """Executes JS_ATOMIC_READ_ONLY_MOUNTED_TARGET_GEOMETRY_SNAPSHOT in Node.js with deterministic DOM fixtures."""

    def _execute_snapshot_in_node(self, fixture_cfg: dict[str, Any]) -> dict[str, Any]:
        cfg_json = json.dumps(fixture_cfg)
        node_script = f"""
const JS_SNAPSHOT = {mgr1.JS_ATOMIC_READ_ONLY_MOUNTED_TARGET_GEOMETRY_SNAPSHOT};
const cfg = {cfg_json};

function runFixture() {{
    global.window = {{
        innerWidth: cfg.windowWidth || 1280,
        innerHeight: cfg.windowHeight || 800,
        visualViewport: cfg.visualViewport ? {{
            width: cfg.visualViewport.width || 1280,
            height: cfg.visualViewport.height || 800,
            offsetLeft: 0,
            offsetTop: 0,
            scale: 1.0
        }} : null,
        devicePixelRatio: 1.0,
        location: {{ href: "https://app.slack.com/client/T042WS00000/C042WS25QAX" }}
    }};
    global.URL = require("url").URL;

    if (cfg.searchViewNotFound) {{
        global.document = {{
            querySelector: () => null,
            documentElement: {{ clientWidth: 1280, clientHeight: 800 }}
        }};
        return JS_SNAPSHOT(
            {json.dumps(mgr1.SEARCH_VIEW_SELECTOR)},
            {json.dumps(mgr1.SEARCH_RESULT_ROW_SELECTORS)},
            {mgr1.MAX_MOUNTED_ROWS_TO_INSPECT},
            "{mgr1.TARGET_CHANNEL_ID}",
            "{mgr1.TARGET_MESSAGE_TS}",
            "{mgr1.TARGET_THREAD_TS}",
            {json.dumps(mgr1.FROZEN_ALLOWED_ORIGINS)}
        );
    }}

    let hitTargetElement = null;

    const rowConfigs = cfg.rows || [];
    const mockRows = rowConfigs.map((rc, idx) => {{
        const isConn = rc.connected !== false;
        const width = rc.positiveGeometry !== false ? (rc.width || 400) : 0;
        const height = rc.positiveGeometry !== false ? (rc.height || 50) : 0;
        const top = rc.top !== undefined ? rc.top : idx * 60;

        const rowObj = {{
            isConnected: isConn,
            getBoundingClientRect: () => ({{
                left: 100,
                top: top,
                right: 100 + width,
                bottom: top + height,
                width: width,
                height: height
            }}),
            parentElement: null
        }};

        const mockAnchors = (rc.anchors || []).map(ac => {{
            const aW = ac.width !== undefined ? ac.width : 50;
            const aH = ac.height !== undefined ? ac.height : 20;
            const aTop = ac.top !== undefined ? ac.top : top + 10;
            const aLeft = ac.left !== undefined ? ac.left : 120;
            const anchorObj = {{
                tagName: "A",
                isConnected: isConn,
                href: ac.href,
                parentElement: rowObj,
                getBoundingClientRect: () => ({{
                    left: aLeft,
                    top: aTop,
                    right: aLeft + aW,
                    bottom: aTop + aH,
                    width: aW,
                    height: aH
                }}),
                contains: (other) => {{
                    if (ac.descendant && other === ac.descendant) return true;
                    return other === anchorObj;
                }}
            }};
            if (ac.isHitTarget) {{
                hitTargetElement = anchorObj;
            }}
            if (ac.descendantHit) {{
                const desc = {{ parentElement: anchorObj }};
                ac.descendant = desc;
                hitTargetElement = desc;
            }}
            return anchorObj;
        }});

        rowObj.querySelectorAll = (sel) => {{
            if (sel === "a[href]") return mockAnchors;
            return [];
        }};

        return rowObj;
    }});

    // Mock parent scroll container
    const scrollContainer = {{
        clientHeight: cfg.scrollContainer ? cfg.scrollContainer.clientHeight : 700,
        clientWidth: cfg.scrollContainer ? cfg.scrollContainer.clientWidth : 600,
        scrollHeight: cfg.scrollContainer ? cfg.scrollContainer.scrollHeight : 2000,
        scrollWidth: cfg.scrollContainer ? cfg.scrollContainer.scrollWidth : 600,
        scrollTop: 0,
        scrollLeft: 0,
        getBoundingClientRect: () => ({{
            left: 50,
            top: 50,
            right: 650,
            bottom: 750,
            width: 600,
            height: 700
        }}),
        parentElement: null
    }};

    mockRows.forEach(r => {{
        r.parentElement = scrollContainer;
    }});

    const mockSearchView = {{
        isConnected: true,
        parentElement: scrollContainer,
        querySelectorAll: (sel) => {{
            if (sel.includes("data-qa=\\"search_result\\"") || sel.includes(".c-search_result")) {{
                return mockRows;
            }}
            return [];
        }},
        contains: (el) => true
    }};

    global.window.getComputedStyle = (el) => {{
        if (el === scrollContainer) {{
            return {{ overflowY: "auto", overflowX: "hidden" }};
        }}
        if (cfg.clippingAncestor && el === scrollContainer) {{
            return {{ overflowY: "hidden", overflowX: "hidden" }};
        }}
        return {{ overflowY: "visible", overflowX: "visible" }};
    }};

    global.document = {{
        querySelector: (sel) => {{
            if (sel === {json.dumps(mgr1.SEARCH_VIEW_SELECTOR)}) return mockSearchView;
            return null;
        }},
        documentElement: {{ clientWidth: 1280, clientHeight: 800 }},
        elementFromPoint: (x, y) => {{
            if (cfg.hitTestNull) return null;
            if (cfg.hitTestUnrelated) return {{ id: "unrelated_element", contains: () => false }};
            return hitTargetElement;
        }}
    }};

    return JS_SNAPSHOT(
        {json.dumps(mgr1.SEARCH_VIEW_SELECTOR)},
        {json.dumps(mgr1.SEARCH_RESULT_ROW_SELECTORS)},
        {mgr1.MAX_MOUNTED_ROWS_TO_INSPECT},
        "{mgr1.TARGET_CHANNEL_ID}",
        "{mgr1.TARGET_MESSAGE_TS}",
        "{mgr1.TARGET_THREAD_TS}",
        {json.dumps(mgr1.FROZEN_ALLOWED_ORIGINS)}
    );
}}

console.log(JSON.stringify(runFixture()));
"""
        proc = subprocess.run(
            ["node", "-"],
            input=node_script,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout.strip())

    def test_node_search_view_not_found(self):
        res = self._execute_snapshot_in_node({"searchViewNotFound": True})
        assert res["ok"] is False
        assert res["geometry_classification"] == "MOUNTED_DOM_STATE_INSUFFICIENT"

    def test_node_target_absent(self):
        res = self._execute_snapshot_in_node({
            "rows": [
                {"anchors": [{"href": "https://app.slack.com/archives/C0000000000/p1700000000000000"}]}
            ]
        })
        assert res["ok"] is True
        assert res["target_match_count"] == 0
        assert res["geometry_classification"] == "TARGET_NOT_FOUND_IN_CONNECTED_MOUNTED_DOM"

    def test_node_target_below_viewport(self):
        res = self._execute_snapshot_in_node({
            "rows": [
                {
                    "top": 1200,
                    "anchors": [{
                        "href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                        "top": 1220,
                        "left": 200,
                        "width": 80,
                        "height": 20
                    }]
                }
            ]
        })
        assert res["ok"] is True
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 1
        assert res["target_anchor_geometry"]["anchor_center_y"] == 1230.0
        assert res["geometry_classification"] == "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT"

    def test_node_target_outside_scroll_viewport(self):
        res = self._execute_snapshot_in_node({
            "rows": [
                {
                    "top": 760,
                    "anchors": [{
                        "href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                        "top": 760,
                        "left": 200,
                        "width": 80,
                        "height": 20
                    }]
                }
            ]
        })
        assert res["ok"] is True
        assert res["geometry_classification"] == "TARGET_ANCHOR_OUTSIDE_SCROLL_CONTAINER_VIEWPORT"

    def test_node_target_element_from_point_null(self):
        res = self._execute_snapshot_in_node({
            "hitTestNull": True,
            "rows": [
                {
                    "top": 200,
                    "anchors": [{
                        "href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                        "top": 210,
                        "left": 200,
                        "width": 80,
                        "height": 20
                    }]
                }
            ]
        })
        assert res["ok"] is True
        assert res["hit_test"]["element_from_point_is_null"] is True
        assert res["geometry_classification"] == "TARGET_ANCHOR_CENTER_IN_VIEWPORT_ELEMENT_FROM_POINT_NULL"

    def test_node_target_occluded(self):
        res = self._execute_snapshot_in_node({
            "hitTestUnrelated": True,
            "rows": [
                {
                    "top": 200,
                    "anchors": [{
                        "href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                        "top": 210,
                        "left": 200,
                        "width": 80,
                        "height": 20
                    }]
                }
            ]
        })
        assert res["ok"] is True
        assert res["hit_test"]["anchor_contains_element_from_point"] is False
        assert res["geometry_classification"] == "TARGET_ANCHOR_OCCLUDED_WITHIN_VIEWPORT"

    def test_node_target_visible_and_reachable(self):
        res = self._execute_snapshot_in_node({
            "rows": [
                {
                    "top": 200,
                    "anchors": [{
                        "href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                        "top": 210,
                        "left": 200,
                        "width": 80,
                        "height": 20,
                        "isHitTarget": True
                    }]
                }
            ]
        })
        assert res["ok"] is True
        assert res["hit_test"]["anchor_contains_element_from_point"] is True
        assert res["geometry_classification"] == "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"

    def test_node_target_descendant_reachable(self):
        res = self._execute_snapshot_in_node({
            "rows": [
                {
                    "top": 200,
                    "anchors": [{
                        "href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                        "top": 210,
                        "left": 200,
                        "width": 80,
                        "height": 20,
                        "descendantHit": True
                    }]
                }
            ]
        })
        assert res["ok"] is True
        assert res["hit_test"]["anchor_contains_element_from_point"] is True
        assert res["hit_test"]["element_from_point_is_anchor_descendant"] is True
        assert res["geometry_classification"] == "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"


# ===========================================================================
# 6. Sanitization & Dry-Run
# ===========================================================================


    def test_node_scroll_container_detection_algorithm(self):
        res = self._execute_snapshot_in_node({
            "scrollContainer": {
                "clientHeight": 500,
                "clientWidth": 400,
                "scrollHeight": 1500,
                "scrollWidth": 400
            },
            "rows": [
                {
                    "top": 100,
                    "anchors": [{
                        "href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                        "top": 110,
                        "left": 120,
                        "width": 80,
                        "height": 20
                    }]
                }
            ]
        })
        assert res["ok"] is True
        sc = res["scroll_container"]
        assert sc["scroll_container_found"] is True
        assert sc["scroll_container_is_document_scroller"] is False
        assert sc["scroll_container_client_height"] == 500
        assert sc["scroll_container_scroll_height"] == 1500

    def test_node_clipping_ancestor_detection(self):
        res = self._execute_snapshot_in_node({
            "clippingAncestor": True,
            "rows": [
                {
                    "top": 100,
                    "anchors": [{
                        "href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                        "top": 110,
                        "left": 120,
                        "width": 80,
                        "height": 20
                    }]
                }
            ]
        })
        assert res["ok"] is True
        assert res["clipping"]["clipping_ancestor_present"] is True

    def test_node_anchor_missing_in_target_row(self):
        res = self._execute_snapshot_in_node({
            "rows": [
                {
                    "top": 100,
                    "anchors": [
                        # row matches target identity on first anchor
                        {"href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"}
                    ]
                }
            ]
        })
        # When 1 valid anchor exists, it qualifies
        assert res["target_anchor_geometry"]["qualified_target_anchor_count"] == 1

        # Now when anchor has mismatching channel
        res2 = self._execute_snapshot_in_node({
            "rows": [
                {
                    "top": 100,
                    "anchors": [
                        {"href": "https://app.slack.com/archives/C9999999999/p1718079276126469?thread_ts=1718058683.912369"}
                    ]
                }
            ]
        })
        assert res2["target_match_count"] == 0

    def test_node_anchor_duplicate_in_target_row(self):
        res = self._execute_snapshot_in_node({
            "rows": [
                {
                    "top": 100,
                    "anchors": [
                        {"href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"},
                        {"href": "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"}
                    ]
                }
            ]
        })
        assert res["target_anchor_geometry"]["qualified_target_anchor_count"] == 2
        assert res["geometry_classification"] == "TARGET_ROW_FOUND_ANCHOR_NOT_UNIQUE"


class TestMgr1SanitizationAndDryRun:
    """Verifies dry-run execution, output schema, and strict persistence sanitization."""

    def test_dry_run_execution_and_verdict(self):
        rep = mgr1.run_mgr1_diagnostic(dry_run=True)
        assert rep["verdict"] == "DRY_RUN_PASS"
        assert rep["diagnostic_verdict"] == "DRY_RUN_PASS"
        assert rep["geometry_classification"] == "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT"
        assert rep["error"] is None
        assert rep["action_contracts"]["MAX_SEARCH_SUBMISSIONS"] == 1
        assert rep["live_action_inventory"]["search_submissions"] == 0

    def test_persisted_dry_run_artifact_sanitization(self):
        assert mgr1.DRY_RUN_OUTPUT_FILE.exists()
        text = mgr1.DRY_RUN_OUTPUT_FILE.read_text(encoding="utf-8")
        data = json.loads(text)

        urls = re.findall(r"https?://[^\s\"]+", text)
        assert len(urls) == 0, f"Found raw URLs in dry run artifact: {urls}"

        archives = re.findall(r"/archives/[^\s\"]+", text)
        assert len(archives) == 0, f"Found raw /archives/ in dry run artifact: {archives}"

        forbidden_keys = [
            "canonical_permalink",
            "result_url",
            "sender_text",
            "normalized_body",
            "raw_href",
            "raw_permalink",
            "message_body",
            "qualifying_permalinks",
        ]
        found_keys = []

        def check_obj(o, p=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    cur = f"{p}.{k}" if p else k
                    if k.lower() in forbidden_keys:
                        found_keys.append((cur, v))
                    check_obj(v, cur)
            elif isinstance(o, list):
                for idx, item in enumerate(o):
                    check_obj(item, f"{p}[{idx}]")

        check_obj(data)
        assert len(found_keys) == 0, f"Found forbidden sensitive fields: {found_keys}"
