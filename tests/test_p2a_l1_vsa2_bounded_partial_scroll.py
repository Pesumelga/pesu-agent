"""Tests for Phase 13.5D / Task 11 / P2A / VSA2: Bounded Partial Scroll Progress Qualification.

Covers:
- Parity & static safety contracts (zero-click, zero-keyboard, zero-navigation, one-scroll only)
- 26 historical baselines & protocol freeze governance
- Canonical identity parsing & UNPROVEN query membership isolation
- Partial scroll calculation, 3-viewport frozen bound preservation, maxScrollTop clamping
- Boundary conditions: full_reveal <= 3x viewports -> FULL_REVEAL_NOW_WITHIN_VSA1_BOUND
- Post-scroll reacquisition (tolerating virtualization and mounted position changes)
- Failure modes: target lost, target duplicated, anchor invalid, tolerance failure, no progress
- Progress proof: distance_below_viewport_after < distance_below_viewport_before
- Offscreen-after-scroll validity & unexpected visibility handling
- Residual distance telemetry computation (read-only, no residual scroll)
- Sensitive data sanitization audit
- Mocked end-to-end offline live-path integration
"""

import copy
import json
import re
from pathlib import Path
from typing import Any
import pytest

import scripts.qualify_task11q4_p2a_l1_bounded_partial_scroll_vsa2 as vsa2


class TestVsa2ParityAndStaticContracts:
    """Verifies static code contracts, runtime API parity, and AST signatures."""

    def test_search_result_row_enumeration_parity(self):
        assert vsa2.verify_search_result_row_enumeration_parity() is True

    def test_runtime_search_path_parity(self):
        assert vsa2.verify_runtime_search_path_parity_with_attempt4() is True

    def test_search_telemetry_authority_parity(self):
        assert vsa2.verify_search_telemetry_authority_parity_with_attempt4() is True

    def test_search_exception_order_parity(self):
        assert vsa2.verify_search_exception_order_parity_with_attempt4() is True

    def test_search_safety_gate_parity(self):
        assert vsa2.verify_search_safety_gate_parity_with_attempt4() is True

    def test_host_dom_evaluation_api_parity(self):
        assert vsa2.verify_host_dom_evaluation_api_parity() is True

    def test_runtime_api_signature_parity(self):
        assert vsa2.verify_runtime_api_signature_parity() is True

    def test_static_zero_interaction_contract(self):
        assert vsa2.verify_static_zero_interaction_contract() is True

    def test_one_scroll_only_contract(self):
        assert vsa2.verify_one_scroll_only_contract() is True

    def test_static_zero_click_in_runner_and_js(self):
        assert ".click()" not in vsa2.JS_READ_ONLY_MOUNTED_DOM_OBSERVATION
        assert ".click()" not in vsa2.JS_APPLY_BOUNDED_PARTIAL_SCROLL_MUTATION
        assert "KeyboardEvent" not in vsa2.JS_READ_ONLY_MOUNTED_DOM_OBSERVATION
        assert "KeyboardEvent" not in vsa2.JS_APPLY_BOUNDED_PARTIAL_SCROLL_MUTATION
        assert "window.scrollTo" not in vsa2.JS_READ_ONLY_MOUNTED_DOM_OBSERVATION
        assert "window.scrollTo" not in vsa2.JS_APPLY_BOUNDED_PARTIAL_SCROLL_MUTATION
        assert "window.scrollBy" not in vsa2.JS_READ_ONLY_MOUNTED_DOM_OBSERVATION
        assert "window.scrollBy" not in vsa2.JS_APPLY_BOUNDED_PARTIAL_SCROLL_MUTATION
        assert "scrollIntoView" not in vsa2.JS_READ_ONLY_MOUNTED_DOM_OBSERVATION
        assert "scrollIntoView" not in vsa2.JS_APPLY_BOUNDED_PARTIAL_SCROLL_MUTATION


class TestVsa2HistoricalAndGovernance:
    """Verifies all 26 historical baselines, protocol freeze, and runtime closure."""

    def test_all_26_historical_baselines_present_and_unmutated(self):
        assert vsa2.PROTOCOL_FREEZE_PATH.exists()
        manifest = json.loads(vsa2.PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))
        baselines = manifest.get("historical_baselines", {})
        assert len(baselines) == 26, f"Expected 26 historical baselines, got {len(baselines)}"

        # Verify VSA1 marker and live are included
        assert "vsa1_marker" in baselines
        assert "vsa1_live" in baselines
        assert baselines["vsa1_marker"]["sha256"] == "dc451367ef99a5be729b135a316c030a3bc2c54a5f16fda43f13e9eda2a624f8"
        assert baselines["vsa1_live"]["sha256"] == "5050c0713cc0aed1bbd2b57ee29ccf052d9e792b9843a793b701753fc57400f1"

        for b_name, b_info in baselines.items():
            b_path = vsa2.REPO_ROOT / b_info["path"]
            assert b_path.exists(), f"Baseline file missing: {b_name} at {b_path}"
            curr_hash = vsa2.get_file_sha256(b_path)
            assert curr_hash == b_info["sha256"], f"Hash mismatch for {b_name}"

    def test_runtime_closure_audit(self):
        manifest = json.loads(vsa2.PROTOCOL_FREEZE_PATH.read_text(encoding="utf-8"))
        closure_res = vsa2.audit_runtime_import_closure(manifest)
        assert closure_res["closure_audit_passed"] is True
        assert len(closure_res["missing_from_closure"]) == 0
        assert len(closure_res["stale_in_closure"]) == 0
        assert len(closure_res["mismatched_hashes"]) == 0

    def test_vsa2_marker_and_live_absent_before_execution(self):
        assert not vsa2.ATTEMPT_STARTED_MARKER_FILE.exists()
        assert not vsa2.LIVE_OUTPUT_FILE.exists()


class TestVsa2CanonicalIdentityAndMembership:
    """Verifies target identity resolution and query membership preservation."""

    def test_target_identity_constants(self):
        assert vsa2.TARGET_CHANNEL_ID == "C042WS25QAX"
        assert vsa2.TARGET_MESSAGE_TS == "1718079276.126469"
        assert vsa2.TARGET_THREAD_TS == "1718058683.912369"
        assert vsa2.TARGET_IS_THREAD_REPLY is True

    def test_membership_remains_unproven(self):
        mock_backend = vsa2.build_mock_dry_run_backend_results()
        b_set, stats = vsa2.build_canonical_backend_identity_set(mock_backend)
        assert stats["backend_result_count"] == 5

        membership = vsa2.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=vsa2.CANONICAL_TARGET_IDENTITY,
            backend_identity_set=b_set,
        )
        assert membership == "UNPROVEN"


class TestVsa2PartialScrollCalculationAndSafetyBound:
    """Verifies partial scroll math, 3-viewport frozen bound preservation, and clamping."""

    def test_vsa1_historical_geometry_partial_scroll(self):
        """Simulates the exact VSA1 live conditions:
        visible_bottom = 857 - 32 = 825
        required_delta = 3349.859375 - 825 = 2524.859375
        max_scroll_delta = 3 * 817 = 2451.0
        -> full_reveal_fits_within_bound is False
        -> partial_scroll_delta = 2451.0
        """
        calc = vsa2.compute_bounded_partial_scroll_target(
            current_scroll_top=0.0,
            scroll_height=3661.0,
            client_height=817.0,
            anchor_rect_top=3332.265625,
            anchor_rect_bottom=3349.859375,
            scroll_container_rect_top=40.0,
            scroll_container_rect_bottom=857.0,
            margin_px=32.0,
            max_delta_multiplier=3.0,
        )
        assert calc["visible_bottom"] == 825.0
        assert calc["full_reveal_required_delta"] == 2524.859375
        assert calc["max_scroll_delta"] == 2451.0
        assert calc["full_reveal_fits_within_bound"] is False
        assert calc["is_already_visible"] is False
        assert calc["partial_requested_scroll_top"] == 2451.0
        assert calc["partial_target_scroll_top"] == 2451.0
        assert calc["partial_scroll_delta"] == 2451.0
        assert calc["is_valid_partial_delta"] is True

    def test_full_reveal_fits_within_bound_boundary(self):
        """If full reveal requires exactly or less than 3 viewports, VSA2 is not applicable."""
        # Case A: exactly 3 viewports (2451.0)
        calc_exact = vsa2.compute_bounded_partial_scroll_target(
            current_scroll_top=0.0,
            scroll_height=3661.0,
            client_height=817.0,
            anchor_rect_top=3258.0,
            anchor_rect_bottom=825.0 + 2451.0,  # 3276.0 -> delta = 2451.0
            scroll_container_rect_top=40.0,
            scroll_container_rect_bottom=857.0,
            margin_px=32.0,
            max_delta_multiplier=3.0,
        )
        assert calc_exact["full_reveal_required_delta"] == 2451.0
        assert calc_exact["full_reveal_fits_within_bound"] is True

        # Case B: less than 3 viewports (e.g. 1000.0)
        calc_less = vsa2.compute_bounded_partial_scroll_target(
            current_scroll_top=0.0,
            scroll_height=3661.0,
            client_height=817.0,
            anchor_rect_top=1800.0,
            anchor_rect_bottom=1825.0,  # delta = 1825 - 825 = 1000.0
            scroll_container_rect_top=40.0,
            scroll_container_rect_bottom=857.0,
            margin_px=32.0,
            max_delta_multiplier=3.0,
        )
        assert calc_less["full_reveal_required_delta"] == 1000.0
        assert calc_less["full_reveal_fits_within_bound"] is True

    def test_partial_scroll_max_scroll_top_clamping(self):
        """If partial requested scroll exceeds max_scroll_top, it clamps to max_scroll_top."""
        # scroll_height 2000, client_height 817 -> max_scroll_top = 1183.0
        # max_scroll_delta = 2451.0 -> partial_requested = 2451.0
        # clamped target = 1183.0
        calc = vsa2.compute_bounded_partial_scroll_target(
            current_scroll_top=0.0,
            scroll_height=2000.0,
            client_height=817.0,
            anchor_rect_top=3500.0,
            anchor_rect_bottom=3518.0,
            scroll_container_rect_top=40.0,
            scroll_container_rect_bottom=857.0,
            margin_px=32.0,
            max_delta_multiplier=3.0,
        )
        assert calc["max_scroll_top"] == 1183.0
        assert calc["partial_target_scroll_top"] == 1183.0
        assert calc["partial_scroll_delta"] == 1183.0
        assert calc["is_valid_partial_delta"] is True


class TestVsa2ResidualDistanceTelemetry:
    """Verifies that residual distance is computed strictly as read-only telemetry."""

    def test_residual_distance_after_partial_scroll(self):
        """After 2451px scroll:
        anchor_rect_bottom = 3349.859375 - 2451 = 898.859375
        visible_bottom = 857 - 32 = 825
        residual_delta = 898.859375 - 825 = 73.859375
        residual_requested_scroll_top = 2451 + 73.859375 = 2524.859375
        """
        res = vsa2.compute_residual_distance_telemetry(
            post_scroll_top=2451.0,
            post_scroll_height=3661.0,
            post_client_height=817.0,
            post_anchor_rect_bottom=898.859375,
            post_scroll_container_rect_bottom=857.0,
            margin_px=32.0,
        )
        assert res["residual_visible_bottom"] == 825.0
        assert abs(res["residual_required_delta"] - 73.859375) < 1e-6
        assert abs(res["residual_requested_scroll_top"] - 2524.859375) < 1e-6
        assert res["residual_max_scroll_top"] == 2844.0


class TestVsa2PostScrollClassificationHierarchy:
    """Verifies all failure and success branches in post-scroll state evaluation."""

    def test_success_with_progress_proven_offscreen(self):
        pre_dom = vsa2.build_mock_dry_run_dom_snapshot(
            case="below_viewport_normal",
            simulated_position=19,
            scroll_top=0.0,
            anchor_rect_top=3332.265625,
            anchor_rect_bottom=3349.859375,
        )
        post_dom = vsa2.build_mock_dry_run_dom_snapshot(
            case="post_scroll_below_viewport_progress",
            simulated_position=19,
            scroll_top=2451.0,
            anchor_rect_top=881.265625,
            anchor_rect_bottom=898.859375,
        )

        cls, details = vsa2.classify_vsa2_post_scroll_state(
            pre_scroll_telemetry=pre_dom,
            post_scroll_telemetry=post_dom,
            scroll_top_before=0.0,
            scroll_top_after=2451.0,
            partial_target_scroll_top=2451.0,
        )
        assert cls == "BOUNDED_PARTIAL_SCROLL_APPLIED_TARGET_REACQUIRED_AND_PROGRESS_CONFIRMED"
        assert details["scroll_top_movement_valid"] is True
        assert details["target_match_count"] == 1
        assert details["anchor_valid"] is True
        assert details["geometric_progress_proven"] is True
        assert details["target_became_visible"] is False
        assert details["distance_below_viewport_before"] == 3332.265625 - 857.0
        assert details["distance_below_viewport_after"] == 881.265625 - 857.0
        assert details["progress_delta_px"] == 2451.0

    def test_success_with_progress_and_unexpected_visibility(self):
        """If partial scroll unexpectedly makes the target visible, progress is proven and visibility noted."""
        pre_dom = vsa2.build_mock_dry_run_dom_snapshot(
            case="below_viewport_normal",
            simulated_position=19,
            scroll_top=0.0,
            anchor_rect_top=3332.265625,
            anchor_rect_bottom=3349.859375,
        )
        post_dom = vsa2.build_mock_dry_run_dom_snapshot(
            case="post_scroll_visible_and_reachable",
            simulated_position=19,
            scroll_top=2451.0,
            anchor_rect_top=800.0,
            anchor_rect_bottom=818.0,
        )

        cls, details = vsa2.classify_vsa2_post_scroll_state(
            pre_scroll_telemetry=pre_dom,
            post_scroll_telemetry=post_dom,
            scroll_top_before=0.0,
            scroll_top_after=2451.0,
            partial_target_scroll_top=2451.0,
        )
        assert cls == "BOUNDED_PARTIAL_SCROLL_APPLIED_TARGET_REACQUIRED_AND_PROGRESS_CONFIRMED"
        assert details["geometric_progress_proven"] is True
        assert details["target_became_visible"] is True

    def test_tolerates_virtualization_and_mounted_position_drift(self):
        """Target row moves from position 19 to position 12 after scroll."""
        pre_dom = vsa2.build_mock_dry_run_dom_snapshot(
            case="below_viewport_normal",
            simulated_position=19,
            scroll_top=0.0,
            anchor_rect_top=3332.265625,
            anchor_rect_bottom=3349.859375,
        )
        post_dom = vsa2.build_mock_dry_run_dom_snapshot(
            case="post_scroll_below_viewport_progress",
            simulated_position=12,  # Drifted due to virtualization!
            scroll_top=2451.0,
            anchor_rect_top=881.265625,
            anchor_rect_bottom=898.859375,
        )

        cls, details = vsa2.classify_vsa2_post_scroll_state(
            pre_scroll_telemetry=pre_dom,
            post_scroll_telemetry=post_dom,
            scroll_top_before=0.0,
            scroll_top_after=2451.0,
            partial_target_scroll_top=2451.0,
        )
        assert cls == "BOUNDED_PARTIAL_SCROLL_APPLIED_TARGET_REACQUIRED_AND_PROGRESS_CONFIRMED"
        assert details["target_match_count"] == 1
        assert details["geometric_progress_proven"] is True

    def test_failure_target_lost_after_scroll(self):
        pre_dom = vsa2.build_mock_dry_run_dom_snapshot(case="below_viewport_normal", simulated_position=19)
        post_dom = vsa2.build_mock_dry_run_dom_snapshot(case="target_lost", simulated_position=19)

        cls, _ = vsa2.classify_vsa2_post_scroll_state(
            pre_scroll_telemetry=pre_dom,
            post_scroll_telemetry=post_dom,
            scroll_top_before=0.0,
            scroll_top_after=2451.0,
            partial_target_scroll_top=2451.0,
        )
        assert cls == "TARGET_LOST_AFTER_PARTIAL_SCROLL"

    def test_failure_target_duplicated_after_scroll(self):
        pre_dom = vsa2.build_mock_dry_run_dom_snapshot(case="below_viewport_normal", simulated_position=19)
        post_dom = vsa2.build_mock_dry_run_dom_snapshot(case="target_duplicated", simulated_position=19)

        cls, _ = vsa2.classify_vsa2_post_scroll_state(
            pre_scroll_telemetry=pre_dom,
            post_scroll_telemetry=post_dom,
            scroll_top_before=0.0,
            scroll_top_after=2451.0,
            partial_target_scroll_top=2451.0,
        )
        assert cls == "TARGET_DUPLICATED_AFTER_PARTIAL_SCROLL"

    def test_failure_scroll_position_not_applied(self):
        pre_dom = vsa2.build_mock_dry_run_dom_snapshot(case="below_viewport_normal", simulated_position=19)
        post_dom = vsa2.build_mock_dry_run_dom_snapshot(case="post_scroll_below_viewport_progress", simulated_position=19)

        # scroll didn't move
        cls_no_move, _ = vsa2.classify_vsa2_post_scroll_state(
            pre_scroll_telemetry=pre_dom,
            post_scroll_telemetry=post_dom,
            scroll_top_before=0.0,
            scroll_top_after=0.0,
            partial_target_scroll_top=2451.0,
        )
        assert cls_no_move == "PARTIAL_SCROLL_POSITION_NOT_APPLIED_AS_REQUESTED"

        # scroll moved but misses tolerance by > 2px
        cls_tol, _ = vsa2.classify_vsa2_post_scroll_state(
            pre_scroll_telemetry=pre_dom,
            post_scroll_telemetry=post_dom,
            scroll_top_before=0.0,
            scroll_top_after=2400.0,
            partial_target_scroll_top=2451.0,
        )
        assert cls_tol == "PARTIAL_SCROLL_POSITION_NOT_APPLIED_AS_REQUESTED"

    def test_failure_no_geometric_progress_after_scroll(self):
        """Scroll position changed, but anchor top did not get closer to scroll container bottom."""
        pre_dom = vsa2.build_mock_dry_run_dom_snapshot(
            case="below_viewport_normal",
            simulated_position=19,
            anchor_rect_top=3332.265625,
        )
        # Anchor top remains at 3332.265625 (no progress)
        post_dom = vsa2.build_mock_dry_run_dom_snapshot(
            case="post_scroll_below_viewport_progress",
            simulated_position=19,
            anchor_rect_top=3332.265625,
        )

        cls, details = vsa2.classify_vsa2_post_scroll_state(
            pre_scroll_telemetry=pre_dom,
            post_scroll_telemetry=post_dom,
            scroll_top_before=0.0,
            scroll_top_after=2451.0,
            partial_target_scroll_top=2451.0,
        )
        assert cls == "NO_GEOMETRIC_PROGRESS_AFTER_PARTIAL_SCROLL"
        assert details["geometric_progress_proven"] is False


class TestVsa2SanitizationAndDryRun:
    """Verifies sensitive data stripping and complete dry-run execution."""

    def test_sanitize_report_removes_sensitive_data(self):
        raw = {
            "canonical_permalink": "https://slack.com/archives/C123/p456",
            "result_url": "https://slack.com/archives/C123/p456",
            "message_body": "Secret confidential message text",
            "sender_text": "Alice Smith",
            "safe_url": "https://app.slack.com/client/T123/C123",
            "archive_ref": "Reference to /archives/C042WS25QAX/p1718079276126469 in log",
            "allowed_data": {"count": 1, "value": 123.45},
        }
        sanitized = vsa2.sanitize_report_for_persistence(raw)
        assert "canonical_permalink" not in sanitized
        assert "result_url" not in sanitized
        assert "message_body" not in sanitized
        assert "sender_text" not in sanitized
        assert sanitized["safe_url"] == "[URL_OMITTED]"
        assert sanitized["archive_ref"] == "Reference to [ARCHIVE_PATH_OMITTED] in log"
        assert sanitized["allowed_data"]["count"] == 1

    def test_dry_run_execution(self):
        report = vsa2.run_vsa2_qualification(dry_run=True)
        assert report["verdict"] == "DRY_RUN_PASS"
        assert report["diagnostic_verdict"] == "BOUNDED_PARTIAL_SCROLL_APPLIED_TARGET_REACQUIRED_AND_PROGRESS_CONFIRMED"
        assert report["geometry_classification"] == "BOUNDED_PARTIAL_SCROLL_APPLIED_TARGET_REACQUIRED_AND_PROGRESS_CONFIRMED"
        assert report["error"] is None

        # Verify output artifact created and sanitized
        assert vsa2.DRY_RUN_OUTPUT_FILE.exists()
        raw_text = vsa2.DRY_RUN_OUTPUT_FILE.read_text(encoding="utf-8")
        assert "https://" not in raw_text
        assert "http://" not in raw_text
        assert "/archives/" not in raw_text


class TestVsa2OfflineLivePathIntegration:
    """Verifies that the live path can execute end-to-end under mock conditions."""

    def test_mocked_live_path_success(self, tmp_path, monkeypatch):
        mock_backend_results = vsa2.build_mock_dry_run_backend_results()
        backend_id_set, _ = vsa2.build_canonical_backend_identity_set(mock_backend_results)

        calc = vsa2.compute_bounded_partial_scroll_target(
            current_scroll_top=0.0,
            scroll_height=3661.0,
            client_height=817.0,
            anchor_rect_top=3332.265625,
            anchor_rect_bottom=3349.859375,
            scroll_container_rect_top=40.0,
            scroll_container_rect_bottom=857.0,
            margin_px=32.0,
            max_delta_multiplier=3.0,
        )
        target_top = calc["partial_target_scroll_top"]

        mock_pre_dom = vsa2.build_mock_dry_run_dom_snapshot(
            case="below_viewport_normal",
            simulated_position=19,
            backend_identity_set=backend_id_set,
            anchor_rect_top=3332.265625,
            anchor_rect_bottom=3349.859375,
        )
        mock_mut_res = {
            "ok": True,
            "scroll_top_before": 0.0,
            "scroll_top_immediate": target_top,
            "partial_target_scroll_top": target_top,
        }
        mock_post_dom = vsa2.build_mock_dry_run_dom_snapshot(
            case="post_scroll_below_viewport_progress",
            simulated_position=19,
            backend_identity_set=backend_id_set,
            scroll_top=target_top,
            anchor_rect_top=881.265625,
            anchor_rect_bottom=898.859375,
        )

        class MockHost:
            eval_count = 0
            def __init__(self, initial_url="about:blank", user_data_folder=None, x=-32000, y=-32000, width=1280, height=900, title="PESU Dedicated Host Runtime"): pass
            def evaluate_js(self, script, timeout=10.0):
                if "scrollContainerEl.scrollTop = partialTargetScrollTop" in script:
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
            def stop_runtime(self): pass
            def search_messages(self, query, max_results=5):
                return mock_backend_results

        # Patch WebView host and backend
        monkeypatch.setattr(vsa2, "PesuWebViewHost", MockHost)
        monkeypatch.setattr(vsa2, "WebViewSlackSearchBackend", MockBackend)

        # Isolate output files to tmp_path to avoid creating real marker/live
        mock_marker = tmp_path / "mock_marker.json"
        mock_live = tmp_path / "mock_live.json"
        monkeypatch.setattr(vsa2, "ATTEMPT_STARTED_MARKER_FILE", mock_marker)
        monkeypatch.setattr(vsa2, "LIVE_OUTPUT_FILE", mock_live)

        report = vsa2.run_vsa2_qualification(dry_run=False, timeout=5.0)

        assert report["verdict"] == "QUALIFICATION_COMPLETED"
        assert report["diagnostic_verdict"] == "BOUNDED_PARTIAL_SCROLL_APPLIED_TARGET_REACQUIRED_AND_PROGRESS_CONFIRMED"
        assert report["geometry_classification"] == "BOUNDED_PARTIAL_SCROLL_APPLIED_TARGET_REACQUIRED_AND_PROGRESS_CONFIRMED"
        assert report["error"] is None
        assert report["live_action_inventory"]["search_submissions"] == 1
        assert report["live_action_inventory"]["scroll_actions"] == 1
        assert report["live_action_inventory"]["post_scroll_dom_snapshots"] == 1
        assert report["live_action_inventory"]["guarded_anchor_clicks"] == 0
        assert report["live_action_inventory"]["keyboard_events"] == 0
        assert mock_marker.exists()
        assert mock_live.exists()
