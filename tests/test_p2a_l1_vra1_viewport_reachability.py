"""Tests for Phase 13.5D / Task 11 / P2A / VRA1 Viewport Reachability Diagnostic.

Strict qualification suite verifying:
- Production row enumeration parity
- Exact target row & anchor identity & uniqueness
- Viewport reachability classifications (below, above, left, right, scroll-outside, clipped, occluded, null, reachable)
- Nearest scroll container and clipping ancestor detection
- Distinction between layout viewport and scroll container viewport
- Static zero-click, zero-scroll, zero-keyboard, zero-DOM mutation contracts
- Numeric geometry persisted, raw content excluded
- One-shot governance and replay prevention
- TAIQ1 immutable baseline exact hashes and timestamps
- Exact-set runtime closure verification
- Production src/ diff empty
- Dry run passes with zero live actions
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

import diagnose_task11q4_p2a_l1_viewport_reachability_vra1 as vra1


class TestVra1ParityAndStaticContracts:
    def test_production_row_enumeration_parity(self):
        assert vra1.verify_search_result_row_enumeration_parity() is True

    def test_container_and_row_selectors_match_production(self):
        assert vra1.SEARCH_VIEW_SELECTOR == vra1.PROD_SEARCH_VIEW_SELECTOR
        assert vra1.SEARCH_RESULT_ROW_SELECTORS == vra1.PROD_SEARCH_RESULT_ROW_SELECTORS

    def test_runtime_search_path_parity(self):
        assert vra1.verify_runtime_search_path_parity_with_attempt4() is True

    def test_search_telemetry_authority_parity(self):
        assert vra1.verify_search_telemetry_authority_parity_with_attempt4() is True

    def test_search_exception_order_parity(self):
        assert vra1.verify_search_exception_order_parity_with_attempt4() is True

    def test_search_safety_gate_parity(self):
        assert vra1.verify_search_safety_gate_parity_with_attempt4() is True

    def test_host_dom_evaluation_api_parity(self):
        assert vra1.verify_host_dom_evaluation_api_parity() is True

    def test_static_zero_interaction_audit(self):
        assert vra1.verify_static_zero_interaction_contract() is True

    def test_no_executable_click_in_js(self):
        js = vra1.JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT
        assert not re.search(r"\.click\s*\(", js)
        assert not re.search(r"\bMouseEvent\b", js)
        assert not re.search(r"\bInputEvent\b", js)

    def test_no_executable_scroll_in_js(self):
        js = vra1.JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT
        assert not re.search(r"\bscrollIntoView\b", js)
        assert not re.search(r"\bscrollTo\b", js)
        assert not re.search(r"\bscrollBy\b", js)
        assert not re.search(r"\bscrollTop\s*=", js)
        assert not re.search(r"\bscrollLeft\s*=", js)
        assert not re.search(r"\bwheel\b", js)

    def test_no_keyboard_events_in_js(self):
        js = vra1.JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT
        assert not re.search(r"\bKeyboardEvent\b", js)
        assert not re.search(r"\bdispatchEvent\b", js)

    def test_no_dom_mutation_in_js(self):
        js = vra1.JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT
        assert not re.search(r"\blocation\s*=", js)
        assert not re.search(r"\blocation\.href\s*=", js)
        assert not re.search(r"\bhistory\.push", js)
        assert not re.search(r"\bhistory\.replace", js)
        assert not re.search(r"\.submit\s*\(", js)

    def test_action_contracts_zeros(self):
        ac = vra1.protocol_manifest = json.loads(vra1.PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))["action_contracts"]
        assert ac.get("MAX_GUARDED_ANCHOR_CLICKS") == 0
        assert ac.get("MAX_REPLY_BAR_CLICKS") == 0
        assert ac.get("MAX_RESULT_CARD_CLICKS") == 0
        assert ac.get("MAX_SCROLL_ACTIONS") == 0
        assert ac.get("MAX_KEYBOARD_EVENTS") == 0
        assert ac.get("MAX_COMPOSER_INTERACTIONS") == 0
        assert ac.get("MAX_WRITE_CAPABLE_ACTIONS") == 0
        assert ac.get("MAX_PROVIDER_CALLS") == 0
        assert ac.get("MAX_RETRIES") == 0
        assert ac.get("MAX_RESTORATIONS") == 0
        assert ac.get("MAX_BACK_NAVIGATIONS") == 0
        assert ac.get("MAX_GEOMETRY_SNAPSHOTS") == 1
        assert ac.get("MAX_SLACK_SEARCH_OPERATIONS") == 1
        assert ac.get("MAX_SEARCH_SUBMISSIONS") == 1


class TestVra1HistoricalAndGovernance:
    def test_taiq1_attempt_marker_sha256_exact(self):
        marker_p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_taiq1_attempt_started.json"
        assert marker_p.exists()
        actual_sha = vra1.get_file_sha256(marker_p)
        assert actual_sha == "48f6709bea9b8718fa5f9bf8dbebe5d5290d395e6d9eb59556a56a48ffc64c4e"
        assert actual_sha == vra1.TAIQ1_ATTEMPT_MARKER_SHA256

    def test_taiq1_live_artifact_sha256_exact(self):
        artifact_p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_taiq1_anchor_interaction.json"
        assert artifact_p.exists()
        actual_sha = vra1.get_file_sha256(artifact_p)
        assert actual_sha == "a59b7c5b2aa04fd137efea688d563a8c9a26afc03b770d6683b46ec4bbfc9037"
        assert actual_sha == vra1.TAIQ1_LIVE_ARTIFACT_SHA256

    def test_all_14_historical_baselines_exact(self):
        with open(vra1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        hb = manifest["historical_baselines"]
        assert len(hb) == 14
        for name, info in hb.items():
            p = REPO_ROOT / info["path"]
            assert p.exists(), f"Historical baseline file missing: {p}"
            actual_sha = vra1.get_file_sha256(p)
            assert actual_sha == info["sha256"], f"SHA256 mismatch for {name}: expected {info['sha256']}, got {actual_sha}"

    def test_marker_and_live_artifact_absent_during_prep(self):
        assert not vra1.ATTEMPT_STARTED_MARKER_FILE.exists(), "VRA1 attempt marker must be absent during prep"
        assert not vra1.LIVE_OUTPUT_FILE.exists(), "VRA1 live artifact must be absent during prep"

    def test_pre_existing_marker_blocks_replay(self, tmp_path, monkeypatch):
        fake_marker = tmp_path / "fake_marker.json"
        fake_marker.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(vra1, "ATTEMPT_STARTED_MARKER_FILE", fake_marker)
        monkeypatch.setattr(vra1, "LIVE_OUTPUT_FILE", tmp_path / "absent_live.json")
        res = vra1.run_vra1_diagnostic(dry_run=False)
        assert res["verdict"] == "FAIL_CLOSED"
        assert res["diagnostic_verdict"] == "ATTEMPT_ALREADY_CONSUMED"

    def test_pre_existing_live_artifact_blocks_replay(self, tmp_path, monkeypatch):
        fake_live = tmp_path / "fake_live.json"
        fake_live.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(vra1, "LIVE_OUTPUT_FILE", fake_live)
        monkeypatch.setattr(vra1, "ATTEMPT_STARTED_MARKER_FILE", tmp_path / "absent_marker.json")
        res = vra1.run_vra1_diagnostic(dry_run=False)
        assert res["verdict"] == "FAIL_CLOSED"
        assert res["diagnostic_verdict"] == "LIVE_ARTIFACT_ALREADY_EXISTS"

    def test_runtime_closure_exact_set(self):
        with open(vra1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        audit = vra1.audit_runtime_import_closure(manifest)
        assert audit["closure_audit_passed"] is True
        assert audit["missing_from_runtime_closure"] == []
        assert audit["stale_runtime_closure_entries"] == []
        assert audit["mismatched_runtime_dependencies"] == []

    def test_sensitive_data_contract_all_false(self):
        with open(vra1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        sc = manifest["sensitive_content_contract"]
        for k, v in sc.items():
            assert v is False, f"Sensitive contract key {k} must be False"

    def test_dry_run_produces_dry_run_pass(self):
        res = vra1.run_vra1_diagnostic(dry_run=True)
        assert res["verdict"] == "DRY_RUN_PASS"
        assert res["diagnostic_verdict"] == "DRY_RUN_PASS"
        inv = res["live_action_inventory"]
        assert inv["search_submissions"] == 0
        assert inv["geometry_snapshots"] == 0
        assert inv["guarded_anchor_clicks"] == 0
        assert inv["scroll_actions"] == 0
        assert res["provider_calls"] == 0
        assert res["simulation_geometry"] is not None
        assert res["geometry_telemetry"] is None

    def test_production_src_diff_empty(self):
        res = subprocess.run(
            ["git", "diff", "HEAD", "--", "src/"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert res.stdout.strip() == "", f"Production src/ diff must be empty: {res.stdout}"


class TestVra1PythonReachabilityClassifier:
    def test_geometry_invalid_on_zero_dimensions(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": False,
            "anchor_rect_width": 0,
            "anchor_rect_height": 0,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_GEOMETRY_INVALID"

    def test_anchor_below_layout_viewport(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": True,
            "anchor_rect_width": 100,
            "anchor_rect_height": 20,
            "anchor_center_inside_layout_viewport": False,
            "anchor_center_x": 400,
            "anchor_center_y": 850,
            "layout_viewport_width": 1280,
            "layout_viewport_height": 800,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT"

    def test_anchor_above_layout_viewport(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": True,
            "anchor_rect_width": 100,
            "anchor_rect_height": 20,
            "anchor_center_inside_layout_viewport": False,
            "anchor_center_x": 400,
            "anchor_center_y": -10,
            "layout_viewport_width": 1280,
            "layout_viewport_height": 800,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_ABOVE_LAYOUT_VIEWPORT"

    def test_anchor_left_of_layout_viewport(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": True,
            "anchor_rect_width": 100,
            "anchor_rect_height": 20,
            "anchor_center_inside_layout_viewport": False,
            "anchor_center_x": -5,
            "anchor_center_y": 200,
            "layout_viewport_width": 1280,
            "layout_viewport_height": 800,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_LEFT_OF_LAYOUT_VIEWPORT"

    def test_anchor_right_of_layout_viewport(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": True,
            "anchor_rect_width": 100,
            "anchor_rect_height": 20,
            "anchor_center_inside_layout_viewport": False,
            "anchor_center_x": 1300,
            "anchor_center_y": 200,
            "layout_viewport_width": 1280,
            "layout_viewport_height": 800,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_RIGHT_OF_LAYOUT_VIEWPORT"

    def test_anchor_outside_scroll_container_viewport(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": True,
            "anchor_rect_width": 100,
            "anchor_rect_height": 20,
            "anchor_center_inside_layout_viewport": True,
            "anchor_center_x": 400,
            "anchor_center_y": 600,
            "layout_viewport_width": 1280,
            "layout_viewport_height": 800,
            "scroll_container_found": True,
            "anchor_center_inside_scroll_viewport": False,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_OUTSIDE_SCROLL_CONTAINER_VIEWPORT"

    def test_anchor_clipped_by_ancestor(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": True,
            "anchor_rect_width": 100,
            "anchor_rect_height": 20,
            "anchor_center_inside_layout_viewport": True,
            "anchor_center_x": 400,
            "anchor_center_y": 600,
            "layout_viewport_width": 1280,
            "layout_viewport_height": 800,
            "scroll_container_found": True,
            "anchor_center_inside_scroll_viewport": True,
            "clipping_ancestor_present": True,
            "anchor_center_inside_all_clipping_ancestors": False,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_CLIPPED_BY_ANCESTOR"

    def test_element_from_point_anchor_passes(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": True,
            "anchor_rect_width": 100,
            "anchor_rect_height": 20,
            "anchor_center_inside_layout_viewport": True,
            "anchor_center_x": 400,
            "anchor_center_y": 300,
            "layout_viewport_width": 1280,
            "layout_viewport_height": 800,
            "scroll_container_found": True,
            "anchor_center_inside_scroll_viewport": True,
            "clipping_ancestor_present": False,
            "anchor_center_inside_all_clipping_ancestors": True,
            "element_from_point_evaluated": True,
            "element_from_point_is_null": False,
            "element_from_point_is_anchor": True,
            "element_from_point_is_anchor_descendant": False,
            "anchor_contains_element_from_point": True,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"

    def test_element_from_point_descendant_passes(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": True,
            "anchor_rect_width": 100,
            "anchor_rect_height": 20,
            "anchor_center_inside_layout_viewport": True,
            "anchor_center_x": 400,
            "anchor_center_y": 300,
            "layout_viewport_width": 1280,
            "layout_viewport_height": 800,
            "scroll_container_found": True,
            "anchor_center_inside_scroll_viewport": True,
            "clipping_ancestor_present": False,
            "anchor_center_inside_all_clipping_ancestors": True,
            "element_from_point_evaluated": True,
            "element_from_point_is_null": False,
            "element_from_point_is_anchor": False,
            "element_from_point_is_anchor_descendant": True,
            "anchor_contains_element_from_point": True,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"

    def test_element_from_point_unrelated_node_occluded(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": True,
            "anchor_rect_width": 100,
            "anchor_rect_height": 20,
            "anchor_center_inside_layout_viewport": True,
            "anchor_center_x": 400,
            "anchor_center_y": 300,
            "layout_viewport_width": 1280,
            "layout_viewport_height": 800,
            "scroll_container_found": True,
            "anchor_center_inside_scroll_viewport": True,
            "clipping_ancestor_present": False,
            "anchor_center_inside_all_clipping_ancestors": True,
            "element_from_point_evaluated": True,
            "element_from_point_is_null": False,
            "element_from_point_is_anchor": False,
            "element_from_point_is_anchor_descendant": False,
            "anchor_contains_element_from_point": False,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_OCCLUDED_WITHIN_VIEWPORT"

    def test_element_from_point_null_in_viewport_anomaly(self):
        snap = {
            "ok": True,
            "anchor_positive_geometry": True,
            "anchor_rect_width": 100,
            "anchor_rect_height": 20,
            "anchor_center_inside_layout_viewport": True,
            "anchor_center_x": 400,
            "anchor_center_y": 300,
            "layout_viewport_width": 1280,
            "layout_viewport_height": 800,
            "scroll_container_found": True,
            "anchor_center_inside_scroll_viewport": True,
            "clipping_ancestor_present": False,
            "anchor_center_inside_all_clipping_ancestors": True,
            "element_from_point_evaluated": True,
            "element_from_point_is_null": True,
            "element_from_point_is_anchor": False,
        }
        assert vra1.classify_viewport_reachability(snap) == "TARGET_ANCHOR_CENTER_IN_VIEWPORT_ELEMENT_FROM_POINT_NULL"


class TestVra1DomFixtureExecution:
    """Executes JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT in Node.js with deterministic DOM fixtures."""

    def _execute_snapshot_in_node(self, fixture_cfg: dict[str, Any]) -> dict[str, Any]:
        cfg_json = json.dumps(fixture_cfg)
        node_script = f"""
const JS_SNAPSHOT = {vra1.JS_ATOMIC_READ_ONLY_GEOMETRY_SNAPSHOT};
const cfg = {cfg_json};

function runFixture() {{
    const windowWidth = cfg.windowWidth || 1280;
    const windowHeight = cfg.windowHeight || 800;

    global.window = {{
        innerWidth: windowWidth,
        innerHeight: windowHeight,
        devicePixelRatio: 1.0,
        location: {{ href: "https://app.slack.com/client/T042WS00000/C042WS25QAX" }},
        visualViewport: cfg.hasVisualViewport ? {{
            width: windowWidth,
            height: windowHeight,
            offsetLeft: 0,
            offsetTop: 0,
            scale: 1.0
        }} : undefined,
        getComputedStyle: (el) => {{
            if (el === mockAnchor) {{
                return {{ display: "inline", visibility: "visible", pointerEvents: "auto" }};
            }}
            if (el === mockScrollContainer) {{
                return {{ overflowY: cfg.scrollerOverflowY || "auto", overflowX: "hidden" }};
            }}
            if (el === mockClippingAncestor) {{
                return {{ overflowY: "hidden", overflowX: "hidden" }};
            }}
            return {{ display: "block", visibility: "visible", pointerEvents: "auto", overflowY: "visible", overflowX: "visible" }};
        }}
    }};

    const mockChildSpan = {{
        tagName: "SPAN",
        isConnected: true,
        contains: () => false
    }};

    const mockUnqualifiedAnchor = {{
        tagName: "A",
        isConnected: true,
        getBoundingClientRect: () => ({{ left: 0, top: 0, width: 0, height: 0, bottom: 0, right: 0 }}),
        getAttribute: (attr) => {{
            if (attr === 'target') return '_self';
            if (attr === 'href') return 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369';
            return null;
        }},
        hasAttribute: (attr) => false,
        href: 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369',
        closest: (sel) => null,
        contains: (node) => false,
        parentElement: null
    }};

    const anchorRect = cfg.anchorRect || {{ left: 300, top: 200, width: 200, height: 20 }};
    anchorRect.bottom = anchorRect.top + anchorRect.height;
    anchorRect.right = anchorRect.left + anchorRect.width;

    const mockAnchor = {{
        tagName: "A",
        isConnected: !cfg.anchorDisconnected,
        getBoundingClientRect: () => anchorRect,
        getAttribute: (attr) => {{
            if (attr === 'target') return cfg.anchorTarget || '_self';
            if (attr === 'href') return cfg.anchorHref || 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369';
            return null;
        }},
        hasAttribute: (attr) => attr === 'download' ? Boolean(cfg.hasDownloadAttr) : false,
        href: cfg.anchorHref || 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369',
        closest: (sel) => {{
            if (cfg.inComposer && sel.includes("message_input")) return {{}};
            if (cfg.inReplyBar && sel.includes("reply_bar")) return {{}};
            return null;
        }},
        contains: (node) => node === mockChildSpan,
        parentElement: null
    }};

    const rowRect = cfg.rowRect || {{ left: 250, top: 180, width: 600, height: 100 }};
    rowRect.bottom = rowRect.top + rowRect.height;
    rowRect.right = rowRect.left + rowRect.width;

    const mockRow = {{
        isConnected: true,
        getBoundingClientRect: () => rowRect,
        querySelectorAll: (sel) => {{
            if (sel.includes("a[href]")) {{
                if (cfg.duplicateAnchor) return [mockAnchor, mockAnchor];
                if (cfg.absentQualifyingAnchor) return [mockUnqualifiedAnchor];
                return [mockAnchor];
            }}
            return [];
        }},
        parentElement: null
    }};

    const scRect = cfg.scrollerRect || {{ left: 200, top: 50, width: 700, height: 700 }};
    scRect.bottom = scRect.top + scRect.height;
    scRect.right = scRect.left + scRect.width;

    const mockScrollContainer = {{
        scrollHeight: cfg.scrollerScrollHeight || 1500,
        clientHeight: cfg.scrollerClientHeight || 700,
        scrollWidth: 700,
        clientWidth: 700,
        scrollTop: 100,
        scrollLeft: 0,
        getBoundingClientRect: () => scRect,
        parentElement: null
    }};

    const clipRect = cfg.clippingRect || {{ left: 0, top: 0, width: 1280, height: 800 }};
    clipRect.bottom = clipRect.top + clipRect.height;
    clipRect.right = clipRect.left + clipRect.width;

    const mockClippingAncestor = {{
        scrollHeight: 500,
        clientHeight: 500,
        scrollWidth: 500,
        clientWidth: 500,
        scrollTop: 0,
        scrollLeft: 0,
        getBoundingClientRect: () => clipRect,
        parentElement: null
    }};

    const mockSearchView = {{
        isConnected: true,
        querySelectorAll: (sel) => {{
            if (cfg.duplicateTargetRow) return [mockRow, mockRow];
            if (cfg.absentTargetRow) return [];
            return [mockRow];
        }},
        contains: (el) => true,
        scrollHeight: 800,
        clientHeight: 800,
        scrollWidth: 800,
        clientWidth: 800
    }};

    // Ancestor chain linking
    if (cfg.hasClippingAncestor) {{
        mockAnchor.parentElement = mockClippingAncestor;
        mockClippingAncestor.parentElement = cfg.hasScrollContainer ? mockScrollContainer : mockSearchView;
        mockScrollContainer.parentElement = mockSearchView;
    }} else if (cfg.hasScrollContainer) {{
        mockAnchor.parentElement = mockScrollContainer;
        mockScrollContainer.parentElement = mockSearchView;
    }} else {{
        mockAnchor.parentElement = mockRow;
        mockRow.parentElement = mockSearchView;
    }}

    const mockUnrelatedHit = {{
        tagName: "DIV",
        isConnected: true,
        contains: () => false
    }};

    global.document = {{
        querySelector: (sel) => sel.includes("search") ? mockSearchView : null,
        documentElement: {{ clientWidth: windowWidth, clientHeight: windowHeight }},
        body: {{ clientWidth: windowWidth, clientHeight: windowHeight }},
        elementFromPoint: (x, y) => {{
            if (cfg.elementFromPointNull) return null;
            if (cfg.elementFromPointDescendant) return mockChildSpan;
            if (cfg.elementFromPointUnrelated) return mockUnrelatedHit;
            return mockAnchor;
        }}
    }};

    return JS_SNAPSHOT(
        {json.dumps(vra1.SEARCH_VIEW_SELECTOR)},
        {json.dumps(vra1.SEARCH_RESULT_ROW_SELECTORS)},
        5,
        "C042WS25QAX",
        "1718079276.126469",
        "1718058683.912369",
        ["https://app.slack.com", "https://heumlabs.slack.com"]
    );
}}

console.log(JSON.stringify(runFixture()));
"""
        proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)

    def test_target_row_absence_fails(self):
        res = self._execute_snapshot_in_node({"absentTargetRow": True})
        assert res["ok"] is False
        assert res["failure_reason"] == "TARGET_ROW_NOT_FOUND"

    def test_target_row_duplication_fails(self):
        res = self._execute_snapshot_in_node({"duplicateTargetRow": True})
        assert res["ok"] is False
        assert res["failure_reason"] == "TARGET_ROW_IDENTITY_NOT_UNIQUE"

    def test_anchor_absence_fails(self):
        res = self._execute_snapshot_in_node({"absentQualifyingAnchor": True})
        assert res["ok"] is False
        assert res["failure_reason"] == "TARGET_ANCHOR_NOT_FOUND"

    def test_duplicate_anchor_fails(self):
        res = self._execute_snapshot_in_node({"duplicateAnchor": True})
        assert res["ok"] is False
        assert res["failure_reason"] == "ANCHOR_IDENTITY_NOT_UNIQUE"

    def test_anchor_inside_composer_rejected(self):
        res = self._execute_snapshot_in_node({"inComposer": True})
        assert res["ok"] is False
        assert res["failure_reason"] == "TARGET_ANCHOR_NOT_FOUND"
        assert "INSIDE_COMPOSER" in res["guard_failures"]

    def test_anchor_inside_reply_bar_rejected(self):
        res = self._execute_snapshot_in_node({"inReplyBar": True})
        assert res["ok"] is False
        assert res["failure_reason"] == "TARGET_ANCHOR_NOT_FOUND"
        assert "INSIDE_REPLY_BAR" in res["guard_failures"]

    def test_anchor_external_origin_rejected(self):
        res = self._execute_snapshot_in_node({"anchorHref": "https://evil.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"})
        assert res["ok"] is False
        assert "ORIGIN_NOT_ALLOWED" in res["guard_failures"]

    def test_anchor_non_https_rejected(self):
        res = self._execute_snapshot_in_node({"anchorHref": "http://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"})
        assert res["ok"] is False
        assert "PROTOCOL_NOT_HTTPS" in res["guard_failures"]

    def test_anchor_non_self_target_rejected(self):
        res = self._execute_snapshot_in_node({"anchorTarget": "_blank"})
        assert res["ok"] is False
        assert "TARGET_NOT_SELF_SAFE" in res["guard_failures"]

    def test_anchor_download_attribute_rejected(self):
        res = self._execute_snapshot_in_node({"hasDownloadAttr": True})
        assert res["ok"] is False
        assert "DOWNLOAD_ATTRIBUTE_PRESENT" in res["guard_failures"]

    def test_anchor_below_layout_viewport_classification(self):
        # Anchor top: 850 in 800px high viewport
        res = self._execute_snapshot_in_node({
            "windowHeight": 800,
            "anchorRect": {"left": 300, "top": 850, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["anchor_center_inside_layout_viewport"] is False
        assert res["element_from_point_evaluated"] is False  # MUST NOT evaluate elementFromPoint when offscreen!
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT"

    def test_anchor_above_layout_viewport_classification(self):
        res = self._execute_snapshot_in_node({
            "windowHeight": 800,
            "anchorRect": {"left": 300, "top": -50, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["anchor_center_inside_layout_viewport"] is False
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_ABOVE_LAYOUT_VIEWPORT"

    def test_anchor_left_of_layout_viewport_classification(self):
        res = self._execute_snapshot_in_node({
            "windowWidth": 1280,
            "anchorRect": {"left": -150, "top": 200, "width": 100, "height": 20}
        })
        assert res["ok"] is True
        assert res["anchor_center_inside_layout_viewport"] is False
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_LEFT_OF_LAYOUT_VIEWPORT"

    def test_anchor_right_of_layout_viewport_classification(self):
        res = self._execute_snapshot_in_node({
            "windowWidth": 1280,
            "anchorRect": {"left": 1300, "top": 200, "width": 100, "height": 20}
        })
        assert res["ok"] is True
        assert res["anchor_center_inside_layout_viewport"] is False
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_RIGHT_OF_LAYOUT_VIEWPORT"

    def test_anchor_intersects_viewport_but_center_outside(self):
        # Anchor top: 795, height: 20 -> bottom: 815, center_y: 805 in 800px viewport
        res = self._execute_snapshot_in_node({
            "windowHeight": 800,
            "anchorRect": {"left": 300, "top": 795, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["anchor_intersects_layout_viewport"] is True
        assert res["anchor_center_inside_layout_viewport"] is False
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_BELOW_LAYOUT_VIEWPORT"

    def test_anchor_center_inside_viewport_but_partial_clipping(self):
        # Anchor top: 790, height: 20 -> bottom: 810, center_y: 800 (or top: 785 -> center_y: 795 < 800)
        res = self._execute_snapshot_in_node({
            "windowHeight": 800,
            "anchorRect": {"left": 300, "top": 785, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["anchor_center_inside_layout_viewport"] is True
        assert res["anchor_fully_inside_layout_viewport"] is False
        assert res["anchor_intersects_layout_viewport"] is True

    def test_anchor_visible_and_element_from_point_anchor_passes(self):
        res = self._execute_snapshot_in_node({
            "hasVisualViewport": True,
            "anchorRect": {"left": 300, "top": 200, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["element_from_point_evaluated"] is True
        assert res["element_from_point_is_anchor"] is True
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"

    def test_anchor_visible_and_element_from_point_descendant_passes(self):
        res = self._execute_snapshot_in_node({
            "elementFromPointDescendant": True,
            "anchorRect": {"left": 300, "top": 200, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["element_from_point_evaluated"] is True
        assert res["element_from_point_is_anchor_descendant"] is True
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_VISIBLE_AND_HIT_TEST_REACHABLE"

    def test_anchor_in_viewport_element_from_point_unrelated_occluded(self):
        res = self._execute_snapshot_in_node({
            "elementFromPointUnrelated": True,
            "anchorRect": {"left": 300, "top": 200, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["element_from_point_evaluated"] is True
        assert res["element_from_point_is_anchor"] is False
        assert res["anchor_contains_element_from_point"] is False
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_OCCLUDED_WITHIN_VIEWPORT"

    def test_anchor_in_viewport_element_from_point_null_anomaly(self):
        res = self._execute_snapshot_in_node({
            "elementFromPointNull": True,
            "anchorRect": {"left": 300, "top": 200, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["element_from_point_evaluated"] is True
        assert res["element_from_point_is_null"] is True
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_CENTER_IN_VIEWPORT_ELEMENT_FROM_POINT_NULL"

    def test_nearest_vertical_scroll_container_detected(self):
        res = self._execute_snapshot_in_node({
            "hasScrollContainer": True,
            "scrollerClientHeight": 600,
            "scrollerScrollHeight": 1400,
            "scrollerOverflowY": "auto",
            "scrollerRect": {"left": 200, "top": 100, "width": 700, "height": 600},
            "anchorRect": {"left": 300, "top": 200, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["scroll_container_found"] is True
        assert res["scroll_container_client_height"] == 600
        assert res["scroll_container_scroll_height"] == 1400
        assert res["anchor_center_inside_scroll_viewport"] is True

    def test_anchor_below_scroll_viewport_classification(self):
        # Anchor top: 750, scroller rect bottom: 600 -> inside layout viewport (800) but outside scroll container!
        res = self._execute_snapshot_in_node({
            "hasScrollContainer": True,
            "windowHeight": 1000,
            "scrollerRect": {"left": 200, "top": 100, "width": 700, "height": 500},
            "anchorRect": {"left": 300, "top": 750, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["anchor_center_inside_layout_viewport"] is True
        assert res["anchor_center_inside_scroll_viewport"] is False
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_OUTSIDE_SCROLL_CONTAINER_VIEWPORT"

    def test_anchor_clipped_by_ancestor_classification(self):
        # Anchor inside layout viewport, inside scroller, but clipped by intermediate ancestor
        res = self._execute_snapshot_in_node({
            "hasClippingAncestor": True,
            "clippingRect": {"left": 100, "top": 100, "width": 500, "height": 200},  # clipping rect bottom: 300
            "anchorRect": {"left": 300, "top": 350, "width": 200, "height": 20}      # anchor center_y: 360 > 300
        })
        assert res["ok"] is True
        assert res["clipping_ancestor_present"] is True
        assert res["anchor_center_inside_all_clipping_ancestors"] is False
        cls = vra1.classify_viewport_reachability(res)
        assert cls == "TARGET_ANCHOR_CLIPPED_BY_ANCESTOR"

    def test_window_viewport_and_scroll_viewport_kept_distinct(self):
        res = self._execute_snapshot_in_node({
            "hasScrollContainer": True,
            "windowWidth": 1280,
            "windowHeight": 800,
            "scrollerRect": {"left": 250, "top": 100, "width": 600, "height": 500},
            "anchorRect": {"left": 300, "top": 200, "width": 200, "height": 20}
        })
        assert res["ok"] is True
        assert res["layout_viewport_width"] == 1280
        assert res["layout_viewport_height"] == 800
        assert res["scroll_container_rect_left"] == 250
        assert res["scroll_container_rect_top"] == 100
        assert res["scroll_container_client_width"] == 700

    def test_numeric_geometry_persisted_raw_content_not_persisted(self):
        res = self._execute_snapshot_in_node({})
        assert res["ok"] is True
        # Numeric UI geometry is permitted as structural telemetry
        assert isinstance(res["anchor_rect_top"], (int, float))
        assert isinstance(res["anchor_center_x"], (int, float))
        assert isinstance(res["row_rect_width"], (int, float))

        # Raw user message / text / html forbidden
        res_str = json.dumps(res)
        assert "26년 하반기 간담회" not in res_str
        assert "href" not in res
        assert "innerHTML" not in res
        assert "outerHTML" not in res
        assert "innerText" not in res
        assert "textContent" not in res
