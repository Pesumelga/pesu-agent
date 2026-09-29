"""Tests for Phase 13.5D / Task 11 / P2A / VSA1: Bounded Viewport Scroll Safety Qualification 1 - Part 1."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import qualify_task11q4_p2a_l1_bounded_viewport_scroll_vsa1 as vsa1


# ===========================================================================
# 1. Parity & Static Contracts
# ===========================================================================

class TestVsa1ParityAndStaticContracts:
    """Verifies that VSA1 adheres to Attempt 4 parity, zero-interaction rules, and bounded scroll limits."""

    def test_search_telemetry_authority_parity(self):
        assert vsa1.verify_search_telemetry_authority_parity_with_attempt4() is True

    def test_search_exception_order_parity(self):
        assert vsa1.verify_search_exception_order_parity_with_attempt4() is True

    def test_search_safety_gate_parity(self):
        assert vsa1.verify_search_safety_gate_parity_with_attempt4() is True

    def test_runtime_search_path_parity(self):
        assert vsa1.verify_runtime_search_path_parity_with_attempt4() is True

    def test_host_dom_evaluation_api_parity(self):
        assert vsa1.verify_host_dom_evaluation_api_parity() is True

    def test_search_result_row_enumeration_parity(self):
        assert vsa1.verify_search_result_row_enumeration_parity() is True

    def test_runtime_api_signature_parity(self):
        assert vsa1.verify_runtime_api_signature_parity() is True

    def test_static_zero_interaction_contract(self):
        assert vsa1.verify_static_zero_interaction_contract() is True

    def test_verify_one_scroll_only_contract(self):
        assert vsa1.verify_one_scroll_only_contract() is True

    def test_strict_action_contract_limits(self):
        contracts = {
            "MAX_SLACK_SEARCH_OPERATIONS": vsa1.MAX_SLACK_SEARCH_OPERATIONS,
            "MAX_SEARCH_SUBMISSIONS": vsa1.MAX_SEARCH_SUBMISSIONS,
            "MAX_SCROLL_ACTIONS": vsa1.MAX_SCROLL_ACTIONS,
            "MAX_PRE_SCROLL_DOM_EXTRACTIONS": vsa1.MAX_PRE_SCROLL_DOM_EXTRACTIONS,
            "MAX_POST_SCROLL_DOM_SNAPSHOTS": vsa1.MAX_POST_SCROLL_DOM_SNAPSHOTS,
            "MAX_GUARDED_ANCHOR_CLICKS": vsa1.MAX_GUARDED_ANCHOR_CLICKS,
            "MAX_REPLY_BAR_CLICKS": vsa1.MAX_REPLY_BAR_CLICKS,
            "MAX_RESULT_CARD_CLICKS": vsa1.MAX_RESULT_CARD_CLICKS,
            "MAX_THREAD_EXPANSIONS": vsa1.MAX_THREAD_EXPANSIONS,
            "MAX_KEYBOARD_EVENTS": vsa1.MAX_KEYBOARD_EVENTS,
            "MAX_COMPOSER_INTERACTIONS": vsa1.MAX_COMPOSER_INTERACTIONS,
            "MAX_WRITE_CAPABLE_ACTIONS": vsa1.MAX_WRITE_CAPABLE_ACTIONS,
            "MAX_PROVIDER_CALLS": vsa1.MAX_PROVIDER_CALLS,
            "MAX_RETRIES": vsa1.MAX_RETRIES,
            "MAX_RESTORATIONS": vsa1.MAX_RESTORATIONS,
            "MAX_BACK_NAVIGATIONS": vsa1.MAX_BACK_NAVIGATIONS,
        }
        assert contracts["MAX_SLACK_SEARCH_OPERATIONS"] == 1
        assert contracts["MAX_SEARCH_SUBMISSIONS"] == 1
        assert contracts["MAX_SCROLL_ACTIONS"] == 1
        assert contracts["MAX_PRE_SCROLL_DOM_EXTRACTIONS"] == 1
        assert contracts["MAX_POST_SCROLL_DOM_SNAPSHOTS"] == 1
        assert contracts["MAX_GUARDED_ANCHOR_CLICKS"] == 0
        assert contracts["MAX_REPLY_BAR_CLICKS"] == 0
        assert contracts["MAX_RESULT_CARD_CLICKS"] == 0
        assert contracts["MAX_THREAD_EXPANSIONS"] == 0
        assert contracts["MAX_KEYBOARD_EVENTS"] == 0
        assert contracts["MAX_COMPOSER_INTERACTIONS"] == 0
        assert contracts["MAX_WRITE_CAPABLE_ACTIONS"] == 0
        assert contracts["MAX_PROVIDER_CALLS"] == 0
        assert contracts["MAX_RETRIES"] == 0
        assert contracts["MAX_RESTORATIONS"] == 0
        assert contracts["MAX_BACK_NAVIGATIONS"] == 0


# ===========================================================================
# 2. Historical Baselines & One-Shot Governance
# ===========================================================================

class TestVsa1HistoricalAndGovernance:
    """Verifies baseline integrity across all 24 historical artifacts and strict one-shot gates."""

    def test_historical_baseline_count_exact_24(self):
        manifest = json.loads(vsa1.PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))
        baselines = manifest.get("historical_baselines", {})
        assert len(baselines) == 24

    def test_all_24_historical_baselines_integrity(self):
        manifest = json.loads(vsa1.PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))
        baselines = manifest.get("historical_baselines", {})
        for name, info in baselines.items():
            path = REPO_ROOT / info["path"]
            expected_sha = info["sha256"]
            assert path.exists(), f"Historical baseline file missing: {name} at {path}"
            content = path.read_bytes()
            actual_sha = hashlib.sha256(content).hexdigest()
            assert actual_sha == expected_sha, (
                f"Historical baseline hash mismatch for {name}: expected {expected_sha}, got {actual_sha}"
            )

    def test_runtime_closure_exact(self):
        protocol_manifest = json.loads(vsa1.PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))
        closure_audit = vsa1.audit_runtime_import_closure(protocol_manifest)
        assert closure_audit["closure_audit_passed"] is True, f"Runtime closure not passed: {closure_audit}"
        assert closure_audit["missing_from_closure"] == []
        assert closure_audit["stale_in_closure"] == []
        assert closure_audit["mismatched_hashes"] == []

    def test_attempt_marker_file_absence_for_prep(self):
        assert not vsa1.ATTEMPT_STARTED_MARKER_FILE.exists(), (
            f"VSA1 marker file must NOT exist during offline preparation: {vsa1.ATTEMPT_STARTED_MARKER_FILE}"
        )

    def test_live_output_file_absence_for_prep(self):
        assert not vsa1.LIVE_OUTPUT_FILE.exists(), (
            f"VSA1 live output file must NOT exist during offline preparation: {vsa1.LIVE_OUTPUT_FILE}"
        )

    def test_one_shot_replay_forbidden_when_marker_exists(self, tmp_path):
        marker = tmp_path / "mock_marker.json"
        marker.write_text("{}", encoding="utf-8")
        with patch.object(vsa1, "ATTEMPT_STARTED_MARKER_FILE", marker):
            res = vsa1.run_vsa1_qualification(dry_run=False)
            assert res["verdict"] == "FAIL_CLOSED"
            assert res["diagnostic_verdict"] == "ATTEMPT_ALREADY_CONSUMED"

    def test_one_shot_replay_forbidden_when_live_artifact_exists(self, tmp_path):
        live_file = tmp_path / "mock_live.json"
        live_file.write_text("{}", encoding="utf-8")
        with patch.object(vsa1, "LIVE_OUTPUT_FILE", live_file):
            res = vsa1.run_vsa1_qualification(dry_run=False)
            assert res["verdict"] == "FAIL_CLOSED"
            assert res["diagnostic_verdict"] == "LIVE_ARTIFACT_ALREADY_EXISTS"


# ===========================================================================
# 3. Canonical Target Identity & Membership Independence
# ===========================================================================

class TestVsa1CanonicalIdentityAndMembership:
    """Verifies target identity resolution and query membership preservation."""

    def test_canonical_target_constants(self):
        assert vsa1.TARGET_CHANNEL_ID == "C042WS25QAX"
        assert vsa1.TARGET_MESSAGE_TS == "1718079276.126469"
        assert vsa1.TARGET_THREAD_TS == "1718058683.912369"
        assert vsa1.TARGET_QUERY == "26년 하반기 간담회"

    def test_backend_identity_set_building(self):
        results = [
            {
                "channel_id": "C042WS25QAX",
                "message_ts": "1718079276.126469",
                "canonical_permalink": "https://test.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
            },
            {
                "channel_id": "C042WS25QAX",
                "message_ts": "1718080000.123456",
                "canonical_permalink": "https://test.slack.com/archives/C042WS25QAX/p1718080000123456",
            },
            {
                "channel_id": "C042WS25QAX",
                "message_ts": "1718090000.000000",
                "canonical_permalink": None,
            },
        ]
        identities, stats = vsa1.build_canonical_backend_identity_set(results)
        assert ("C042WS25QAX", "1718079276.126469", "1718058683.912369") in identities
        assert ("C042WS25QAX", "1718080000.123456", None) in identities
        assert stats["backend_result_count"] == 3
        assert stats["backend_identity_parse_success_count"] == 2
        assert stats["backend_identity_parse_failure_count"] == 1

    def test_query_membership_remains_unproven(self):
        backend_results = vsa1.build_mock_dry_run_backend_results()
        identity_set, stats = vsa1.build_canonical_backend_identity_set(backend_results)
        dom_snapshot = vsa1.build_mock_dry_run_dom_snapshot(
            case="post_scroll_visible_and_reachable",
            backend_identity_set=identity_set,
        )
        target_rec = next(r for r in dom_snapshot["mounted_row_records"] if r["is_target"])
        assert target_rec["row_current_query_membership"] == "UNPROVEN"

    def test_scroll_success_does_not_promote_membership(self):
        """Membership cannot be promoted from UNPROVEN to PROVEN by scrolling."""
        report = vsa1.run_vsa1_qualification(dry_run=True)
        assert report["target_row_current_query_membership"] == "UNPROVEN"
        post_records = report["post_scroll_telemetry"].get("mounted_row_records", [])
        target_rec = next(r for r in post_records if r["is_target"])
        assert target_rec["row_current_query_membership"] == "UNPROVEN"

    def test_target_position_drift_preserves_identity(self):
        """Virtualized node recycling or row index drift from 17 to 5 does not lose identity."""
        dom_snapshot = vsa1.build_mock_dry_run_dom_snapshot(
            case="post_scroll_visible_and_reachable",
            simulated_position=5,
        )
        assert dom_snapshot["target_match_count"] == 1
        assert dom_snapshot["target_mounted_dom_position"] == 5
        assert dom_snapshot["target_anchor_geometry"]["qualified_target_anchor_count"] == 1


# ===========================================================================
# 4. Scroll Target Strategy & 3-Viewport Safety Bound
# ===========================================================================

class TestVsa1ScrollCalculationAndSafetyBound:
    """Verifies minimal reveal formula, 32px bottom margin, 3-viewport safety bound, and clamping."""

    def test_minimal_reveal_calculation_mgr1r1_observed(self):
        """Observed MGR1-R1 geometry:
        anchor_rect_bottom = 3091.859375
        scroll_container_rect_bottom = 857.0
        client_height = 817.0
        scroll_height = 3661.0
        current_scroll_top = 0.0
        margin = 32.0
        visible_bottom = 857.0 - 32.0 = 825.0
        required_delta = 3091.859375 - 825.0 = 2266.859375
        max_allowed_delta = 3 * 817.0 = 2451.0
        """
        calc = vsa1.compute_bounded_scroll_target(
            current_scroll_top=0.0,
            scroll_height=3661.0,
            client_height=817.0,
            anchor_rect_top=3074.265625,
            anchor_rect_bottom=3091.859375,
            scroll_container_rect_top=40.0,
            scroll_container_rect_bottom=857.0,
            margin_px=32.0,
            max_delta_multiplier=3.0,
        )
        assert calc["visible_bottom"] == 825.0
        assert abs(calc["required_delta"] - 2266.859375) < 1e-4
        assert calc["is_already_visible"] is False
        assert calc["exceeds_bound"] is False
        assert abs(calc["target_scroll_top"] - 2266.859375) < 1e-4
        assert calc["max_allowed_delta"] == 2451.0
        assert calc["planned_delta"] == 2266.859375

    def test_scroll_not_required_target_already_visible(self):
        """When anchor is already inside visible scroll viewport, required_delta <= 0."""
        calc = vsa1.compute_bounded_scroll_target(
            current_scroll_top=500.0,
            scroll_height=3661.0,
            client_height=817.0,
            anchor_rect_top=200.0,
            anchor_rect_bottom=250.0,
            scroll_container_rect_top=40.0,
            scroll_container_rect_bottom=857.0,
            margin_px=32.0,
        )
        assert calc["is_already_visible"] is True
        assert calc["required_delta"] <= 0

    def test_scroll_delta_exceeds_frozen_bound(self):
        """When required delta > 3 * clientHeight, exceeds_bound is True."""
        calc = vsa1.compute_bounded_scroll_target(
            current_scroll_top=0.0,
            scroll_height=10000.0,
            client_height=800.0,
            anchor_rect_top=5000.0,
            anchor_rect_bottom=5050.0,
            scroll_container_rect_top=40.0,
            scroll_container_rect_bottom=840.0,
            margin_px=32.0,
            max_delta_multiplier=3.0,
        )
        # visible_bottom = 840 - 32 = 808
        # required_delta = 5050 - 808 = 4242 > 3 * 800 (2400)
        assert calc["exceeds_bound"] is True
        assert calc["planned_delta"] > calc["max_allowed_delta"]

    def test_clamping_to_max_scroll_top(self):
        """When target_scroll_top exceeds scrollHeight - clientHeight, clamp to maxScrollTop."""
        calc = vsa1.compute_bounded_scroll_target(
            current_scroll_top=0.0,
            scroll_height=2000.0,
            client_height=800.0,
            anchor_rect_top=2000.0,
            anchor_rect_bottom=2020.0,
            scroll_container_rect_top=0.0,
            scroll_container_rect_bottom=800.0,
            margin_px=32.0,
            max_delta_multiplier=3.0,
        )
        # max_scroll_top = 2000 - 800 = 1200
        # visible_bottom = 800 - 32 = 768
        # required_delta = 2020 - 768 = 1252
        # requested_scroll_top = 1252
        # clamped target_scroll_top = 1200
        assert calc["max_scroll_top"] == 1200.0
        assert calc["target_scroll_top"] == 1200.0


# ===========================================================================
# 5. Post-Scroll Classification Hierarchy & Terminal Outcomes
# ===========================================================================

class TestVsa1PostScrollClassificationHierarchy:
    """Verifies all terminal diagnostic classifications defined in the VSA1 contract."""

    def test_classify_scroll_position_not_applied_due_to_no_movement(self):
        res = vsa1.classify_vsa1_post_scroll_state(
            pre_scroll_telemetry={},
            post_scroll_telemetry={"ok": True},
            scroll_top_before=100.0,
            scroll_top_after=100.0,
            target_scroll_top=200.0,
            tolerance_px=2.0,
        )
        assert res == "SCROLL_POSITION_NOT_APPLIED_AS_REQUESTED"

    def test_classify_scroll_position_not_applied_tolerance_exceeded(self):
        res = vsa1.classify_vsa1_post_scroll_state(
            pre_scroll_telemetry={},
            post_scroll_telemetry={"ok": True},
            scroll_top_before=0.0,
            scroll_top_after=150.0,
            target_scroll_top=200.0,
            tolerance_px=2.0,
        )
        assert res == "SCROLL_POSITION_NOT_APPLIED_AS_REQUESTED"

    def test_classify_target_lost_after_scroll(self):
        mock_post = vsa1.build_mock_dry_run_dom_snapshot(case="target_lost")
        res = vsa1.classify_vsa1_post_scroll_state(
            pre_scroll_telemetry={},
            post_scroll_telemetry=mock_post,
            scroll_top_before=0.0,
            scroll_top_after=200.0,
            target_scroll_top=200.0,
        )
        assert res == "TARGET_LOST_AFTER_SCROLL"

    def test_classify_target_duplicated_after_scroll(self):
        mock_post = vsa1.build_mock_dry_run_dom_snapshot(case="target_duplicated")
        res = vsa1.classify_vsa1_post_scroll_state(
            pre_scroll_telemetry={},
            post_scroll_telemetry=mock_post,
            scroll_top_before=0.0,
            scroll_top_after=200.0,
            target_scroll_top=200.0,
        )
        assert res == "TARGET_IDENTITY_DUPLICATED_AFTER_SCROLL"

    def test_classify_target_anchor_invalid_after_scroll(self):
        mock_post = vsa1.build_mock_dry_run_dom_snapshot(case="anchor_invalid")
        res = vsa1.classify_vsa1_post_scroll_state(
            pre_scroll_telemetry={},
            post_scroll_telemetry=mock_post,
            scroll_top_before=0.0,
            scroll_top_after=200.0,
            target_scroll_top=200.0,
        )
        assert res == "TARGET_ANCHOR_INVALID_AFTER_SCROLL"

    def test_classify_target_still_outside_scroll_viewport_after_scroll(self):
        mock_post = vsa1.build_mock_dry_run_dom_snapshot(case="post_scroll_still_outside")
        res = vsa1.classify_vsa1_post_scroll_state(
            pre_scroll_telemetry={},
            post_scroll_telemetry=mock_post,
            scroll_top_before=0.0,
            scroll_top_after=200.0,
            target_scroll_top=200.0,
        )
        assert res == "TARGET_STILL_OUTSIDE_SCROLL_VIEWPORT_AFTER_SCROLL"

    def test_classify_post_scroll_target_clipped(self):
        mock_post = vsa1.build_mock_dry_run_dom_snapshot(case="post_scroll_clipped")
        res = vsa1.classify_vsa1_post_scroll_state(
            pre_scroll_telemetry={},
            post_scroll_telemetry=mock_post,
            scroll_top_before=0.0,
            scroll_top_after=200.0,
            target_scroll_top=200.0,
        )
        assert res == "POST_SCROLL_TARGET_CLIPPED"

    def test_classify_post_scroll_element_from_point_null(self):
        mock_post = vsa1.build_mock_dry_run_dom_snapshot(case="post_scroll_element_from_point_null")
        res = vsa1.classify_vsa1_post_scroll_state(
            pre_scroll_telemetry={},
            post_scroll_telemetry=mock_post,
            scroll_top_before=0.0,
            scroll_top_after=200.0,
            target_scroll_top=200.0,
        )
        assert res == "POST_SCROLL_ELEMENT_FROM_POINT_NULL"

    def test_classify_post_scroll_target_occluded(self):
        mock_post = vsa1.build_mock_dry_run_dom_snapshot(case="post_scroll_occluded")
        res = vsa1.classify_vsa1_post_scroll_state(
            pre_scroll_telemetry={},
            post_scroll_telemetry=mock_post,
            scroll_top_before=0.0,
            scroll_top_after=200.0,
            target_scroll_top=200.0,
        )
        assert res == "POST_SCROLL_TARGET_OCCLUDED"

    def test_classify_post_scroll_success_visible_and_reachable(self):
        mock_post = vsa1.build_mock_dry_run_dom_snapshot(case="post_scroll_visible_and_reachable")
        res = vsa1.classify_vsa1_post_scroll_state(
            pre_scroll_telemetry={},
            post_scroll_telemetry=mock_post,
            scroll_top_before=0.0,
            scroll_top_after=200.0,
            target_scroll_top=200.0,
        )
        assert res == "BOUNDED_SCROLL_APPLIED_TARGET_REACQUIRED_AND_VISIBLE"


# ===========================================================================
# 6. Sanitization & Dry-Run Verification
# ===========================================================================

class TestVsa1SanitizationAndDryRun:
    """Verifies that dry-run artifact generation succeeds with zero sensitive data leaks."""

    def test_dry_run_execution_verdict(self):
        report = vsa1.run_vsa1_qualification(dry_run=True)
        assert report["verdict"] == "DRY_RUN_PASS"
        assert report["diagnostic_verdict"] == "BOUNDED_SCROLL_APPLIED_TARGET_REACQUIRED_AND_VISIBLE"
        assert report["geometry_classification"] == "BOUNDED_SCROLL_APPLIED_TARGET_REACQUIRED_AND_VISIBLE"

    def test_dry_run_artifact_persisted_and_valid(self):
        assert vsa1.DRY_RUN_OUTPUT_FILE.exists()
        data = json.loads(vsa1.DRY_RUN_OUTPUT_FILE.read_text(encoding="utf-8"))
        assert data["verdict"] == "DRY_RUN_PASS"
        assert data["scroll_calculation"]["is_already_visible"] is False
        assert data["scroll_mutation_result"]["ok"] is True
        assert data["scroll_mutation_result"]["scroll_applied_within_tolerance"] is True
        assert data["scroll_mutation_result"]["scroll_top_after"] > data["scroll_mutation_result"]["scroll_top_before"]

    def test_recursive_sensitive_data_audit_on_dry_run(self):
        data = json.loads(vsa1.DRY_RUN_OUTPUT_FILE.read_text(encoding="utf-8"))
        url_pattern = re.compile(r"https?://[^\s\x22\x27]+")
        forbidden_fields = {
            "message_text", "sender_text", "raw_href", "raw_permalink",
            "raw_url", "inner_text", "text_content", "html", "aria_label", "title",
            "innerText", "textContent", "innerHTML", "outerHTML", "ariaLabel"
        }
        url_matches = []
        archives_matches = []
        sensitive_key_matches = []

        def walk(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    curr = f"{path}.{k}" if path else k
                    if k in forbidden_fields:
                        sensitive_key_matches.append((curr, k))
                    walk(v, curr)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    walk(item, f"{path}[{i}]")
            elif isinstance(obj, str):
                for m in url_pattern.finditer(obj):
                    url_matches.append((path, m.group(0)))
                if "/archives/" in obj:
                    archives_matches.append((path, obj))

        walk(data)
        assert len(url_matches) == 0, f"URL findings: {url_matches}"
        assert len(archives_matches) == 0, f"/archives/ findings: {archives_matches}"
        assert len(sensitive_key_matches) == 0, f"Sensitive field findings: {sensitive_key_matches}"


# ===========================================================================
# 7. Mocked Offline Live-Path Integration
# ===========================================================================

class TestVsa1OfflineLivePathIntegration:
    """Verifies that the live path can execute end-to-end under mock conditions."""

    def test_mocked_live_path_success(self, tmp_path, monkeypatch):
        mock_backend_results = vsa1.build_mock_dry_run_backend_results()
        backend_id_set, _ = vsa1.build_canonical_backend_identity_set(mock_backend_results)

        calc = vsa1.compute_bounded_scroll_target(
            current_scroll_top=0.0,
            scroll_height=3661.0,
            client_height=817.0,
            anchor_rect_top=3074.265625,
            anchor_rect_bottom=3091.859375,
            scroll_container_rect_top=40.0,
            scroll_container_rect_bottom=857.0,
            margin_px=32.0,
        )
        target_top = calc["target_scroll_top"]

        mock_pre_dom = vsa1.build_mock_dry_run_dom_snapshot(case="below_viewport_normal", simulated_position=17, backend_identity_set=backend_id_set)
        mock_mut_res = {
            "ok": True,
            "scroll_top_before": 0.0,
            "scroll_top_immediate": target_top,
            "target_scroll_top": target_top,
        }
        mock_post_dom = vsa1.build_mock_dry_run_dom_snapshot(
            case="post_scroll_visible_and_reachable",
            simulated_position=17,
            backend_identity_set=backend_id_set,
            scroll_top=target_top,
        )

        class MockHost:
            eval_count = 0
            def __init__(self, initial_url="about:blank", user_data_folder=None, x=-32000, y=-32000, width=1280, height=900, title="PESU Dedicated Host Runtime"): pass
            def evaluate_js(self, script, timeout=10.0):
                if "scrollContainerEl.scrollTop = targetScrollTop" in script:
                    return mock_mut_res
                elif "target_match_count" in script and "scroll_container" in script:
                    MockHost.eval_count += 1
                    if MockHost.eval_count == 1:
                        return mock_pre_dom
                    return mock_post_dom
                return {"ok": True}
            def destroy(self): pass

        class MockBackend:
            def __init__(self, config=None, default_max_results=10, host=None, search_ui_ready_timeout_sec=5.0, search_ui_ready_poll_interval_sec=0.1, search_transition_timeout_sec=4.0, search_transition_poll_interval_sec=0.2):
                self.last_search_telemetry = {
                    "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": True,
                    "forensic_transition": {
                        "INPUT_MATCHES_REQUESTED": True,
                        "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
                    },
                    "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": False,
                    "slackbot_action_invoked_by_pesu": False,
                    "write_capable_action_invoked_by_pesu": False,
                    "unsafe_non_search_action_invoked_by_pesu": False,
                    "SEARCH_RESULT_READINESS": "RESULTS_READY",
                }
            def start_runtime(self, timeout=30.0): pass
            def search_messages(self, query, max_results=5):
                return mock_backend_results
            def stop_runtime(self): pass

        monkeypatch.setattr(vsa1, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(vsa1, "ATTEMPT_STARTED_MARKER_FILE", tmp_path / "marker.json")
        monkeypatch.setattr(vsa1, "LIVE_OUTPUT_FILE", tmp_path / "live.json")
        monkeypatch.setattr(vsa1, "PesuWebViewHost", MockHost)
        monkeypatch.setattr(vsa1, "WebViewSlackSearchBackend", MockBackend)

        res = vsa1.run_vsa1_qualification(dry_run=False)

        assert res["verdict"] == "QUALIFICATION_COMPLETED"
        assert res["diagnostic_verdict"] == "BOUNDED_SCROLL_APPLIED_TARGET_REACQUIRED_AND_VISIBLE"
        assert res["geometry_classification"] == "BOUNDED_SCROLL_APPLIED_TARGET_REACQUIRED_AND_VISIBLE"
        assert res["live_action_inventory"]["scroll_actions"] == 1
        assert res["live_action_inventory"]["guarded_anchor_clicks"] == 0
        assert res["live_action_inventory"]["keyboard_events"] == 0
        assert res["live_action_inventory"]["composer_interactions"] == 0
        assert res["live_action_inventory"]["write_capable_actions"] == 0
        assert res["provider_calls"] == 0
        assert res["retry_occurred"] is False
        assert (tmp_path / "marker.json").exists()
        assert (tmp_path / "live.json").exists()

    def test_mocked_live_path_scroll_delta_exceeds_bound(self, tmp_path, monkeypatch):
        """When pre-scroll anchor requires delta > 3 viewports, fail closed before scroll."""
        mock_backend_results = vsa1.build_mock_dry_run_backend_results()
        backend_id_set, _ = vsa1.build_canonical_backend_identity_set(mock_backend_results)

        mock_pre_dom = vsa1.build_mock_dry_run_dom_snapshot(case="below_viewport_normal", simulated_position=17, backend_identity_set=backend_id_set)
        mock_pre_dom["target_anchor_geometry"]["anchor_rect_top"] = 6000.0
        mock_pre_dom["target_anchor_geometry"]["anchor_rect_bottom"] = 6020.0
        mock_pre_dom["scroll_container"]["scroll_container_scroll_height"] = 10000.0

        class MockHost:
            def __init__(self, initial_url="about:blank", user_data_folder=None, x=-32000, y=-32000, width=1280, height=900, title="PESU Dedicated Host Runtime"): pass
            def evaluate_js(self, script, timeout=10.0):
                return mock_pre_dom
            def destroy(self): pass

        class MockBackend:
            def __init__(self, config=None, default_max_results=10, host=None, search_ui_ready_timeout_sec=5.0, search_ui_ready_poll_interval_sec=0.1, search_transition_timeout_sec=4.0, search_transition_poll_interval_sec=0.2):
                self.last_search_telemetry = {
                    "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": True,
                    "forensic_transition": {
                        "INPUT_MATCHES_REQUESTED": True,
                        "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
                    },
                    "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": False,
                    "slackbot_action_invoked_by_pesu": False,
                    "write_capable_action_invoked_by_pesu": False,
                    "unsafe_non_search_action_invoked_by_pesu": False,
                    "SEARCH_RESULT_READINESS": "RESULTS_READY",
                }
            def start_runtime(self, timeout=30.0): pass
            def search_messages(self, query, max_results=5):
                return mock_backend_results
            def stop_runtime(self): pass

        monkeypatch.setattr(vsa1, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(vsa1, "ATTEMPT_STARTED_MARKER_FILE", tmp_path / "marker.json")
        monkeypatch.setattr(vsa1, "LIVE_OUTPUT_FILE", tmp_path / "live.json")
        monkeypatch.setattr(vsa1, "PesuWebViewHost", MockHost)
        monkeypatch.setattr(vsa1, "WebViewSlackSearchBackend", MockBackend)

        res = vsa1.run_vsa1_qualification(dry_run=False)

        assert res["verdict"] == "FAIL_CLOSED"
        assert res["diagnostic_verdict"] == "SCROLL_DELTA_EXCEEDS_FROZEN_BOUND"
        assert res["live_action_inventory"]["scroll_actions"] == 0
