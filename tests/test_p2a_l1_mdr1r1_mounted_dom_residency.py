"""Tests for MDR1R1 Mounted DOM Exact Target Residency Diagnostic.

Verifies:
1. Production selector and enumeration parity
2. Zero interaction AST purity (zero click, zero scroll, zero keyboard, zero navigation)
3. Backend max_results remains 5, diagnostic cap is 20
4. Target residency detection at positions 1, 5, 6, 20
5. DOM position is not labeled production/backend rank
6. Connected offscreen rows are qualified for residency regardless of positive geometry
7. Search surface correlation vs per-row query membership semantic split:
   - Search surface correlation does not imply per-row query membership
   - Target at DOM position 6 retains row membership UNPROVEN
   - No unsupported promotion from connected row to current-query result
8. Canonical backend identity extraction & matching:
   - Semantic (channel_id, norm_message_ts, norm_thread_ts) tuple matching
   - DOM position 1 or 5 with mismatch is UNPROVEN
   - DOM position 6 or 20 matching backend identity is PROVEN
   - Target membership uses exact same canonical identity authority
   - No position-based membership heuristic in AST or runtime
9. Exact historical baselines (all 16 entries, including TAIQ1 and VRA1)
10. One-shot replay prevention
11. Sensitive data exclusion
"""

import ast
import json
import os
import subprocess
from pathlib import Path
from typing import Any
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
RUNNER_PATH = SCRIPTS_DIR / "diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1r1.py"

import sys
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import diagnose_task11q4_p2a_l1_mounted_dom_residency_mdr1r1 as mdr1r1


class TestMdr1ParityAndStaticContracts:
    """Verifies production parity and static zero-interaction contracts."""

    def test_production_row_enumeration_parity(self):
        assert mdr1r1.verify_search_result_row_enumeration_parity() is True

    def test_container_and_row_selectors_match_production(self):
        assert mdr1r1.SEARCH_VIEW_SELECTOR == '[data-qa="search_view"]'
        assert mdr1r1.SEARCH_RESULT_ROW_SELECTORS == '[data-qa="search_result"], [data-qa="search_message_result_item"], .c-search_result'

    def test_runtime_search_path_parity(self):
        assert mdr1r1.verify_runtime_search_path_parity_with_attempt4() is True

    def test_search_telemetry_authority_parity(self):
        assert mdr1r1.verify_search_telemetry_authority_parity_with_attempt4() is True

    def test_search_exception_order_parity(self):
        assert mdr1r1.verify_search_exception_order_parity_with_attempt4() is True

    def test_search_safety_gate_parity(self):
        assert mdr1r1.verify_search_safety_gate_parity_with_attempt4() is True

    def test_host_dom_evaluation_api_parity(self):
        assert mdr1r1.verify_host_dom_evaluation_api_parity() is True

    def test_static_zero_interaction_audit(self):
        assert mdr1r1.verify_static_zero_interaction_contract() is True

    def test_no_executable_click_in_js(self):
        code = mdr1r1.JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT
        assert ".click(" not in code
        assert "dispatchEvent" not in code
        assert "MouseEvent" not in code

    def test_no_executable_scroll_in_js(self):
        code = mdr1r1.JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT
        assert "scrollIntoView" not in code
        assert "scrollTo" not in code
        assert "scrollBy" not in code
        assert "scrollTop" not in code
        assert "scrollLeft" not in code

    def test_no_keyboard_or_navigation_in_js(self):
        code = mdr1r1.JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT
        assert "KeyboardEvent" not in code
        assert "InputEvent" not in code
        assert "history.back" not in code
        assert "location.href =" not in code
        assert "fetch(" not in code
        assert "XMLHttpRequest" not in code
        assert "WebSocket" not in code

    def test_backend_search_max_results_remains_5(self):
        assert mdr1r1.SEARCH_MAX_RESULTS == 5

    def test_max_mounted_rows_cap_is_20(self):
        assert mdr1r1.MAX_MOUNTED_ROWS_TO_INSPECT == 20

    def test_diagnostic_snapshot_does_not_slice_to_top5(self):
        code = mdr1r1.JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT
        assert "slice(0, 5)" not in code
        assert "slice(0, maxMountedRows)" in code

    def test_action_contracts_zero_budgets(self):
        assert mdr1r1.MAX_GUARDED_ANCHOR_CLICKS == 0
        assert mdr1r1.MAX_REPLY_BAR_CLICKS == 0
        assert mdr1r1.MAX_RESULT_CARD_CLICKS == 0
        assert mdr1r1.MAX_THREAD_EXPANSIONS == 0
        assert mdr1r1.MAX_SCROLL_ACTIONS == 0
        assert mdr1r1.MAX_KEYBOARD_EVENTS == 0
        assert mdr1r1.MAX_WRITE_CAPABLE_ACTIONS == 0
        assert mdr1r1.MAX_PROVIDER_CALLS == 0
        assert mdr1r1.MAX_RETRIES == 0


class TestMdr1HistoricalAndGovernance:
    """Verifies frozen historical integrity, one-shot replay, and sensitive data contracts."""

    def test_vra1_marker_sha256_exact(self):
        p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_vra1_attempt_started.json"
        assert p.exists()
        assert mdr1r1.get_file_sha256(p) == "cc19a73e2d2da7978218982443147cb890e504d851d945dd4087451195466c59"

    def test_vra1_live_artifact_sha256_exact(self):
        p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_vra1_viewport_reachability.json"
        assert p.exists()
        assert mdr1r1.get_file_sha256(p) == "ae252ecdac609e366fc914a9f6a8645a5b3a108d005bc1600639bc54b48a1032"

    def test_taiq1_baselines_exact(self):
        marker_p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_taiq1_attempt_started.json"
        live_p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_taiq1_anchor_interaction.json"
        assert marker_p.exists() and live_p.exists()
        assert mdr1r1.get_file_sha256(marker_p) == "48f6709bea9b8718fa5f9bf8dbebe5d5290d395e6d9eb59556a56a48ffc64c4e"
        assert mdr1r1.get_file_sha256(live_p) == "a59b7c5b2aa04fd137efea688d563a8c9a26afc03b770d6683b46ec4bbfc9037"

    def test_all_18_historical_baselines_exact(self):
        with open(mdr1r1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        hb = manifest["historical_baselines"]
        assert len(hb) == 18, f"Expected 18 baselines, got {len(hb)}"
        for name, info in hb.items():
            p = REPO_ROOT / info["path"]
            assert p.exists(), f"Historical baseline file missing: {p}"
            act_sha = mdr1r1.get_file_sha256(p)
            assert act_sha == info["sha256"], f"Baseline hash mismatch for {name}: {act_sha} != {info['sha256']}"

    def test_mdr1_marker_and_live_exact_frozen_hashes(self):
        m_path = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_mdr1_attempt_started.json"
        l_path = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_mdr1_mounted_dom_residency.json"
        assert m_path.exists()
        assert l_path.exists()
        assert mdr1r1.get_file_sha256(m_path) == "47ab640493d3004638158cff89f302b2f4224b58f69ca187effbd400ff9b6d70"
        assert mdr1r1.get_file_sha256(l_path) == "5f6fb3baca00dff266e95db5cf69d69fefe186f9f754d0d4bb89f14a16545a6c"

    def test_marker_and_live_artifact_absent_during_prep(self):
        assert not mdr1r1.ATTEMPT_STARTED_MARKER_FILE.exists()
        assert not mdr1r1.LIVE_OUTPUT_FILE.exists()

    def test_pre_existing_marker_blocks_replay(self, tmp_path, monkeypatch):
        fake_marker = tmp_path / "task11q4_p2a_l1_mdr1r1_attempt_started.json"
        fake_marker.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mdr1r1, "ATTEMPT_STARTED_MARKER_FILE", fake_marker)

        rep = mdr1r1.run_mdr1r1_diagnostic(dry_run=False)
        assert rep["verdict"] == "FAIL_CLOSED"
        assert rep["diagnostic_verdict"] == "ATTEMPT_ALREADY_CONSUMED"

    def test_pre_existing_live_artifact_blocks_replay(self, tmp_path, monkeypatch):
        fake_live = tmp_path / "task11q4_p2a_l1_mdr1r1_mounted_dom_residency.json"
        fake_live.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mdr1r1, "LIVE_OUTPUT_FILE", fake_live)

        rep = mdr1r1.run_mdr1r1_diagnostic(dry_run=False)
        assert rep["verdict"] == "FAIL_CLOSED"
        assert rep["diagnostic_verdict"] == "LIVE_ARTIFACT_ALREADY_EXISTS"

    def test_runtime_closure_exact_set(self):
        with open(mdr1r1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        closure = mdr1r1.audit_runtime_import_closure(manifest)
        assert closure["closure_audit_passed"] is True
        assert closure["missing_from_runtime_closure_count"] == 0
        assert closure["stale_runtime_closure_entry_count"] == 0
        assert closure["mismatched_runtime_dependency_count"] == 0

    def test_sensitive_data_contract_all_false(self):
        rep = mdr1r1.run_mdr1r1_diagnostic(dry_run=True)
        checks = rep["checks"]
        assert checks["USER_MESSAGE_TEXT_CAPTURED"] is False
        assert checks["SENDER_TEXT_CAPTURED"] is False
        assert checks["RAW_HREF_CAPTURED"] is False
        assert checks["RAW_PERMALINK_CAPTURED"] is False
        assert checks["RAW_ARIA_LABEL_CAPTURED"] is False
        assert checks["RAW_TITLE_CAPTURED"] is False
        assert checks["INNER_TEXT_CAPTURED"] is False
        assert checks["TEXT_CONTENT_CAPTURED"] is False
        assert checks["INNER_HTML_CAPTURED"] is False
        assert checks["OUTER_HTML_CAPTURED"] is False

    def test_dry_run_produces_dry_run_pass(self):
        rep = mdr1r1.run_mdr1r1_diagnostic(dry_run=True)
        assert rep["verdict"] == "DRY_RUN_PASS"
        assert rep["diagnostic_verdict"] == "DRY_RUN_PASS"
        assert rep["residency_classification"] == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"
        assert rep["search_surface_current_query_correlated"] is True
        assert rep["target_row_current_query_membership"] == "PROVEN"
        assert "backend_identity_telemetry" in rep
        b_tel = rep["backend_identity_telemetry"]
        assert b_tel["backend_result_count"] == 5
        assert b_tel["backend_identity_parse_success_count"] == 5
        assert b_tel["backend_identity_parse_failure_count"] == 0
        inv = rep["live_action_inventory"]
        for k, v in inv.items():
            if isinstance(v, int):
                assert v == 0, f"Non-zero live action in dry run: {k}={v}"

    def test_production_src_diff_empty(self):
        res = subprocess.run(
            ["git", "diff", "HEAD", "--", "src/"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        assert res.stdout.strip() == "", "Production src/ has uncommitted modifications against HEAD!"


class TestMdr1CanonicalIdentityAndMembership:
    """Verifies canonical identity derivation, backend identity set, and semantic membership."""

    @pytest.fixture
    def sample_backend_set(self):
        return {
            ("C042WS25QAX", "1788000000.000001", "1788000000.000001"),
            ("C042WS25QAX", "1788000000.000002", "1788000000.000002"),
            ("C042WS25QAX", "1788000000.000003", "1788000000.000003"),
            ("C042WS25QAX", "1788000000.000004", "1788000000.000004"),
            ("C042WS25QAX", "1788000000.000005", "1788000000.000005"),
        }

    def test_search_surface_correlation_proven(self):
        auth = mdr1r1.evaluate_search_surface_current_query_correlation(
            search_view_connected=True,
            surface_query_matches=True,
            search_readiness="RESULTS_READY",
        )
        assert auth is True

    def test_search_surface_correlation_unproven_query_mismatch(self):
        auth = mdr1r1.evaluate_search_surface_current_query_correlation(
            search_view_connected=True,
            surface_query_matches=False,
            search_readiness="RESULTS_READY",
        )
        assert auth is False

    def test_search_surface_correlation_unproven_not_ready(self):
        auth = mdr1r1.evaluate_search_surface_current_query_correlation(
            search_view_connected=True,
            surface_query_matches=True,
            search_readiness="LOADING",
        )
        assert auth is False

    def test_search_surface_correlation_unproven_disconnected(self):
        auth = mdr1r1.evaluate_search_surface_current_query_correlation(
            search_view_connected=False,
            surface_query_matches=True,
            search_readiness="RESULTS_READY",
        )
        assert auth is False

    def test_dom_position_1_but_backend_identity_mismatch_unproven(self, sample_backend_set):
        # DOM position 1 contains an identity NOT in the backend set
        ident = ("C042WS25QAX", "1788000000.999999", None)
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "UNPROVEN"

    def test_dom_position_5_but_backend_identity_mismatch_unproven(self, sample_backend_set):
        ident = ("C042WS25QAX", "1788000000.888888", "1788000000.888888")
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "UNPROVEN"

    def test_dom_position_6_but_identity_exists_in_backend_set_proven(self, sample_backend_set):
        # Row at position 6 matches backend item 1
        ident = ("C042WS25QAX", "1788000000.000001", "1788000000.000001")
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "PROVEN"

    def test_dom_position_20_but_identity_exists_in_backend_set_proven(self, sample_backend_set):
        # Row at position 20 matches backend item 5
        ident = ("C042WS25QAX", "1788000000.000005", "1788000000.000005")
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "PROVEN"

    def test_same_position_as_backend_array_index_does_not_prove_membership(self, sample_backend_set):
        # Position 2 has identity X, backend item 2 is different
        ident_x = ("C042WS25QAX", "1788000000.000099", None)
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=ident_x,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "UNPROVEN"

    def test_backend_identity_exact_tuple_match_proves_membership(self, sample_backend_set):
        target_in_set = ("C042WS25QAX", "1788000000.000003", "1788000000.000003")
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=target_in_set,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "PROVEN"

    def test_message_ts_mismatch_unproven(self, sample_backend_set):
        ident = ("C042WS25QAX", "1788000000.000009", "1788000000.000001")
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "UNPROVEN"

    def test_thread_ts_mismatch_unproven(self, sample_backend_set):
        ident = ("C042WS25QAX", "1788000000.000001", "1788000000.999999")
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "UNPROVEN"

    def test_channel_id_mismatch_unproven(self, sample_backend_set):
        ident = ("C9999999999", "1788000000.000001", "1788000000.000001")
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "UNPROVEN"

    def test_backend_permalink_parse_failure_no_guessed_membership(self):
        raw_results = [
            {"result_url": "https://example.com/unsupported/link"},
            {"result_url": None},
        ]
        b_set, stats = mdr1r1.build_canonical_backend_identity_set(raw_results)
        assert len(b_set) == 0
        assert stats["backend_result_count"] == 2
        assert stats["backend_identity_parse_success_count"] == 0
        assert stats["backend_identity_parse_failure_count"] == 2

    def test_mounted_row_identity_parse_failure_unproven(self, sample_backend_set):
        row_rec = {"identity_parse_success": False, "channel_id": None, "message_ts": None}
        ident = mdr1r1.build_canonical_mounted_row_identity(row_rec)
        assert ident is None
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "UNPROVEN"

    def test_target_membership_uses_same_canonical_identity_authority(self, sample_backend_set):
        # Target not in backend set
        target_ident = mdr1r1.CANONICAL_TARGET_IDENTITY
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=target_ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "UNPROVEN"

        # If target WAS in backend set, it would be PROVEN
        backend_with_target = set(sample_backend_set)
        backend_with_target.add(target_ident)
        mem_proven = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=target_ident,
            backend_identity_set=backend_with_target,
        )
        assert mem_proven == "PROVEN"

    def test_target_at_position_6_and_absent_backend_set(self, sample_backend_set):
        dom = mdr1r1.build_mock_dry_run_dom_snapshot(simulated_position=6, backend_identity_set=sample_backend_set)
        cls = mdr1r1.classify_mounted_dom_residency(dom)
        assert cls == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"
        # Row 6 is target, absent from backend set -> UNPROVEN
        rec6 = dom["mounted_row_records"][5]
        assert rec6["mounted_dom_position"] == 6
        assert rec6["is_target"] is True
        assert rec6["row_current_query_membership"] == "UNPROVEN"

    def test_target_at_position_6_and_present_backend_set(self):
        backend_set, _ = mdr1r1.build_canonical_backend_identity_set(mdr1r1.build_mock_dry_run_backend_results())
        dom = mdr1r1.build_mock_dry_run_dom_snapshot(simulated_position=6, backend_identity_set=backend_set)
        cls = mdr1r1.classify_mounted_dom_residency(dom)
        assert cls == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"
        # Row 6 is target, present in backend set -> PROVEN
        rec6 = dom["mounted_row_records"][5]
        assert rec6["mounted_dom_position"] == 6
        assert rec6["is_target"] is True
        assert rec6["row_current_query_membership"] == "PROVEN"

    def test_target_at_position_3_and_present_backend_set(self):
        backend_set, _ = mdr1r1.build_canonical_backend_identity_set(mdr1r1.build_mock_dry_run_backend_results())
        dom = mdr1r1.build_mock_dry_run_dom_snapshot(simulated_position=3, backend_identity_set=backend_set)
        cls = mdr1r1.classify_mounted_dom_residency(dom)
        assert cls == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"
        # Row 3 is target, present in backend set -> PROVEN
        rec3 = dom["mounted_row_records"][2]
        assert rec3["mounted_dom_position"] == 3
        assert rec3["is_target"] is True
        assert rec3["row_current_query_membership"] == "PROVEN"

    def test_surface_correlation_true_does_not_upgrade_unmatched_row(self, sample_backend_set):
        unmatched = ("C042WS25QAX", "1788000000.777777", None)
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=unmatched,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "UNPROVEN"

    def test_no_executable_position_based_membership_heuristic(self):
        runner_content = RUNNER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(runner_content)

        # Walk AST and ensure no comparison between position and len(raw_results)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                # Check for patterns like `r_pos <= len(...)`
                left_str = ast.unparse(node.left) if hasattr(ast, "unparse") else ""
                comparators_str = [ast.unparse(c) for c in node.comparators] if hasattr(ast, "unparse") else []
                for comp in comparators_str:
                    if "len(raw_results)" in comp and ("pos" in left_str.lower() or "rank" in left_str.lower()):
                        pytest.fail(f"Forbidden position-based comparison found: {left_str} <= {comp}")
                    if "len(raw_results)" in left_str and ("pos" in comp.lower() or "rank" in comp.lower()):
                        pytest.fail(f"Forbidden position-based comparison found: {left_str} >= {comp}")

    def test_row_membership_stale_when_disconnected(self, sample_backend_set):
        ident = ("C042WS25QAX", "1788000000.000001", "1788000000.000001")
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=False,
            surface_correlated=True,
            row_identity=ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "STALE_OR_RECYCLED_POSSIBLE"

    def test_row_membership_stale_when_surface_uncorrelated(self, sample_backend_set):
        ident = ("C042WS25QAX", "1788000000.000001", "1788000000.000001")
        mem = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=False,
            row_identity=ident,
            backend_identity_set=sample_backend_set,
        )
        assert mem == "STALE_OR_RECYCLED_POSSIBLE"

    def test_target_found_in_connected_mounted_dom(self):
        dom = {"ok": True, "search_view_connected": True, "target_match_count": 1, "mounted_row_count": 20}
        cls = mdr1r1.classify_mounted_dom_residency(dom)
        assert cls == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"

    def test_target_not_found_in_connected_mounted_dom(self):
        dom = {"ok": True, "search_view_connected": True, "target_match_count": 0, "mounted_row_count": 20}
        cls = mdr1r1.classify_mounted_dom_residency(dom)
        assert cls == "TARGET_NOT_FOUND_IN_CONNECTED_MOUNTED_DOM"

    def test_target_identity_duplicated_in_connected_mounted_dom(self):
        dom = {"ok": True, "search_view_connected": True, "target_match_count": 2, "mounted_row_count": 20}
        cls = mdr1r1.classify_mounted_dom_residency(dom)
        assert cls == "TARGET_IDENTITY_DUPLICATED_IN_CONNECTED_MOUNTED_DOM"

    def test_mounted_dom_state_insufficient(self):
        dom = {"ok": True, "search_view_connected": True, "target_match_count": 0, "mounted_row_count": 0}
        cls = mdr1r1.classify_mounted_dom_residency(dom)
        assert cls == "MOUNTED_DOM_STATE_INSUFFICIENT"

    def test_fail_closed_on_snapshot_failure(self):
        assert mdr1r1.classify_mounted_dom_residency(None) == "FAIL_CLOSED"
        assert mdr1r1.classify_mounted_dom_residency({}) == "FAIL_CLOSED"
        assert mdr1r1.classify_mounted_dom_residency({"ok": False}) == "FAIL_CLOSED"
        assert mdr1r1.classify_mounted_dom_residency({"ok": True, "search_view_connected": False}) == "FAIL_CLOSED"

    def test_no_verdict_calls_dom_position_production_rank(self):
        dom = mdr1r1.build_mock_dry_run_dom_snapshot(simulated_position=6)
        cls = mdr1r1.classify_mounted_dom_residency(dom)
        assert "rank" not in cls.lower()
        assert "production" not in cls.lower()
        assert "backend" not in cls.lower()


class TestMdr1NodeDOMInspection:
    """Executes JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT in Node.js with deterministic DOM fixtures."""

    def _execute_snapshot_in_node(self, fixture_cfg: dict[str, Any]) -> dict[str, Any]:
        cfg_json = json.dumps(fixture_cfg)
        node_script = f"""
const JS_SNAPSHOT = {mdr1r1.JS_ATOMIC_READ_ONLY_MOUNTED_DOM_SNAPSHOT};
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
            {json.dumps(mdr1r1.SEARCH_VIEW_SELECTOR)},
            {json.dumps(mdr1r1.SEARCH_RESULT_ROW_SELECTORS)},
            {mdr1r1.MAX_MOUNTED_ROWS_TO_INSPECT},
            "{mdr1r1.TARGET_CHANNEL_ID}",
            "{mdr1r1.TARGET_MESSAGE_TS}",
            "{mdr1r1.TARGET_THREAD_TS}",
            {json.dumps(mdr1r1.FROZEN_ALLOWED_ORIGINS)}
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
            if (sel === {json.dumps(mdr1r1.SEARCH_VIEW_SELECTOR)}) return mockSearchView;
            return null;
        }}
    }};

    return JS_SNAPSHOT(
        {json.dumps(mdr1r1.SEARCH_VIEW_SELECTOR)},
        {json.dumps(mdr1r1.SEARCH_RESULT_ROW_SELECTORS)},
        {mdr1r1.MAX_MOUNTED_ROWS_TO_INSPECT},
        "{mdr1r1.TARGET_CHANNEL_ID}",
        "{mdr1r1.TARGET_MESSAGE_TS}",
        "{mdr1r1.TARGET_THREAD_TS}",
        {json.dumps(mdr1r1.FROZEN_ALLOWED_ORIGINS)}
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
                permalink = f"https://heumlabs.slack.com/archives/{mdr1r1.TARGET_CHANNEL_ID}/p1718079276126469?thread_ts={mdr1r1.TARGET_THREAD_TS}"
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
        assert mdr1r1.classify_mounted_dom_residency(res) == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"

    def test_target_found_at_position_5(self):
        rows = self._build_standard_20_rows(target_position=5)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 5
        assert mdr1r1.classify_mounted_dom_residency(res) == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"

    def test_target_found_at_position_6(self):
        rows = self._build_standard_20_rows(target_position=6)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 6
        assert mdr1r1.classify_mounted_dom_residency(res) == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"

    def test_target_found_at_position_20(self):
        rows = self._build_standard_20_rows(target_position=20)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 20
        assert mdr1r1.classify_mounted_dom_residency(res) == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"

    def test_target_absent(self):
        rows = self._build_standard_20_rows(target_position=None)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 0
        assert res["target_mounted_dom_position"] is None
        assert mdr1r1.classify_mounted_dom_residency(res) == "TARGET_NOT_FOUND_IN_CONNECTED_MOUNTED_DOM"

    def test_target_duplicated(self):
        rows = self._build_standard_20_rows(target_position=3)
        rows[10]["anchors"] = [{
            "href": f"https://heumlabs.slack.com/archives/{mdr1r1.TARGET_CHANNEL_ID}/p1718079276126469?thread_ts={mdr1r1.TARGET_THREAD_TS}"
        }]
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 2
        assert mdr1r1.classify_mounted_dom_residency(res) == "TARGET_IDENTITY_DUPLICATED_IN_CONNECTED_MOUNTED_DOM"

    def test_dom_position_not_labeled_production_rank(self):
        rows = self._build_standard_20_rows(target_position=6)
        res = self._execute_snapshot_in_node({"rows": rows})
        assert "target_mounted_dom_position" in res
        assert "production_rank" not in res
        assert "backend_rank" not in res
        assert "search_rank" not in res
        for rec in res["mounted_row_records"]:
            assert "production_rank" not in rec
            assert "backend_rank" not in rec
            assert "search_rank" not in rec

    def test_connected_offscreen_row_still_inspectable_for_residency(self):
        rows = self._build_standard_20_rows(target_position=15)
        rows[14]["positiveGeometry"] = False  # Rendered offscreen with zero size
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["ok"] is True
        assert res["target_match_count"] == 1
        assert res["target_mounted_dom_position"] == 15
        assert res["target_connected"] is True
        assert res["target_positive_geometry"] is False

    def test_positive_geometry_tracked_separately_from_connection(self):
        rows = self._build_standard_20_rows(target_position=2)
        rows[1]["positiveGeometry"] = False
        res = self._execute_snapshot_in_node({"rows": rows})
        assert res["connected_row_count"] == 20
        assert res["positive_geometry_row_count"] == 19
        rec = res["mounted_row_records"][1]
        assert rec["connected"] is True
        assert rec["positive_geometry"] is False

    def test_raw_href_and_text_never_persisted(self):
        rows = self._build_standard_20_rows(target_position=6)
        res = self._execute_snapshot_in_node({"rows": rows})
        serialized = json.dumps(res)
        assert "https://heumlabs.slack.com" not in serialized
        assert "sender_text" not in serialized
        assert "message_text" not in serialized
        assert "innerHTML" not in serialized
        assert "outerHTML" not in serialized

    def test_search_view_not_found_handled(self):
        res = self._execute_snapshot_in_node({"searchViewNotFound": True})
        assert res["ok"] is False
        assert res["failure_reason"] == "SEARCH_VIEW_NOT_FOUND"
        assert mdr1r1.classify_mounted_dom_residency(res) == "FAIL_CLOSED"


class TestMdr1R1TelemetryAndSanitizationRegressions:
    """Rigorous regressions covering the exact MDR1 failure mode and sanitization requirements."""

    def test_fresh_live_report_supports_harvest_search_telemetry_without_keyerror(self):
        """Verifies that harvest_search_telemetry can be called on a fresh report without KeyError."""
        report = {
            "live_action_inventory": {"search_submissions": 0},
            "checks": {},
            "safety_telemetry": {
                "safe_search_action_explicitly_selected": "UNKNOWN",
                "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": "UNKNOWN",
                "SLACKBOT_ACTION_INVOKED_BY_PESU": "UNKNOWN",
                "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": "UNKNOWN",
                "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": "UNKNOWN",
            },
            "search_telemetry_extracted": {
                "safe_search_action_explicitly_selected": "UNKNOWN",
                "query_state_verified_before_submit": "UNKNOWN",
                "search_surface_query_matches_requested": "UNKNOWN",
                "search_surface_query_after": "UNKNOWN",
            },
        }

        # Mock backend with last_search_telemetry matching MDR1 live telemetry
        mock_backend = type("MockBackend", (), {})()
        mock_backend.last_search_telemetry = {
            "safe_search_action_selected": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked_by_pesu": False,
            "write_capable_action_invoked_by_pesu": False,
            "unsafe_non_search_action_invoked_by_pesu": False,
            "verified_query_state_before_submit": True,
            "SEARCH_RESULT_READINESS": "RESULTS_READY",
            "forensic_transition": {
                "query_submit_triggered": True,
                "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
                "SEARCH_SURFACE_QUERY_AFTER": "다음 결과: 26년 하반기 간담회",
                "raw_result_identifiers": [
                    "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"
                ]
            },
            "raw_items_evaluated": [
                {
                    "raw_index": 1,
                    "canonical_permalink": "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                    "qualifying_permalinks": [
                        "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"
                    ],
                    "sender_text": "이재희",
                    "normalized_body": "본문 테스트",
                    "body_valid": True,
                    "ambiguous_fragment_count": 0,
                    "thread_reply_detected": True,
                    "drop_reason": None,
                }
            ]
        }

        # Should execute cleanly without raising KeyError: 'safety_telemetry'
        mdr1r1.harvest_search_telemetry(mock_backend, None, report)

        assert report["safety_telemetry"]["safe_search_action_explicitly_selected"] is True
        assert report["safety_telemetry"]["GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH"] is False
        assert report["safety_telemetry"]["SLACKBOT_ACTION_INVOKED_BY_PESU"] is False
        assert report["safety_telemetry"]["WRITE_CAPABLE_ACTION_INVOKED_BY_PESU"] is False
        assert report["safety_telemetry"]["UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU"] is False
        assert report["live_action_inventory"]["search_submissions"] == 1

        safe_ok, reason = mdr1r1.evaluate_search_safety_and_query_authority(report)
        assert safe_ok is True
        assert reason == "SAFE"

    def test_defensive_harvest_search_telemetry_when_container_missing(self):
        """Verifies that harvest_search_telemetry creates safety_telemetry if completely omitted."""
        report = {"live_action_inventory": {}}
        mock_backend = type("MockBackend", (), {})()
        mock_backend.last_search_telemetry = {
            "safe_search_action_selected": True,
        }

        mdr1r1.harvest_search_telemetry(mock_backend, None, report)
        assert "safety_telemetry" in report
        assert report["safety_telemetry"]["safe_search_action_explicitly_selected"] is True

    def test_missing_optional_telemetry_remains_unknown_not_exception(self):
        """Verifies missing optional fields do not raise exceptions."""
        report = {
            "live_action_inventory": {},
            "safety_telemetry": {
                "safe_search_action_explicitly_selected": "UNKNOWN",
                "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": "UNKNOWN",
                "SLACKBOT_ACTION_INVOKED_BY_PESU": "UNKNOWN",
                "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": "UNKNOWN",
                "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": "UNKNOWN",
            },
            "search_telemetry_extracted": {
                "safe_search_action_explicitly_selected": "UNKNOWN",
                "query_state_verified_before_submit": "UNKNOWN",
                "search_surface_query_matches_requested": "UNKNOWN",
                "search_surface_query_after": "UNKNOWN",
            },
        }
        mock_backend = type("MockBackend", (), {})()
        mock_backend.last_search_telemetry = {}

        mdr1r1.harvest_search_telemetry(mock_backend, None, report)
        assert report["safety_telemetry"]["safe_search_action_explicitly_selected"] == "UNKNOWN"

    def test_sanitization_removes_all_raw_permalinks_and_sensitive_fields(self):
        """Verifies sanitize_report_for_persistence purges raw URLs and sensitive message text."""
        dirty_report = {
            "task_id": "Task 11 / P2A / MDR1-R1",
            "search_telemetry": {
                "SEARCH_RESULT_READINESS": "RESULTS_READY",
                "forensic_transition": {
                    "raw_result_identifiers": [
                        "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                        "https://heumlabs.slack.com/archives/D07RA9YDE8G/p1788483883410459?thread_ts=1788483883.410459"
                    ]
                },
                "raw_items_evaluated": [
                    {
                        "raw_index": 1,
                        "canonical_permalink": "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                        "qualifying_permalinks": [
                            "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"
                        ],
                        "sender_author_id": "U52PCNUEM",
                        "sender_text": "이재희",
                        "normalized_body": "비밀 메시지 본문",
                        "body_valid": True,
                        "ambiguous_fragment_count": 0,
                        "thread_reply_detected": True,
                        "drop_reason": None,
                    }
                ]
            }
        }

        cleaned = mdr1r1.sanitize_report_for_persistence(dirty_report)

        st = cleaned["search_telemetry"]
        item = st["raw_items_evaluated"][0]
        assert "canonical_permalink" not in item
        assert "qualifying_permalinks" not in item
        assert "sender_text" not in item
        assert "normalized_body" not in item
        assert item["channel_id"] == "C042WS25QAX"
        assert item["message_ts"] == "1718079276.126469"
        assert item["thread_ts"] == "1718058683.912369"
        assert item["identity_parse_success"] is True

        # Check raw_result_identifiers in forensic_transition
        idents = st["forensic_transition"]["raw_result_identifiers"]
        assert idents == [
            "C042WS25QAX:1718079276.126469",
            "D07RA9YDE8G:1788483883.410459"
        ]

        # Recursive check for any URL
        def check_no_url(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    assert "http://" not in k and "https://" not in k
                    check_no_url(v)
            elif isinstance(obj, list):
                for v in obj:
                    check_no_url(v)
            elif isinstance(obj, str):
                assert "http://" not in obj and "https://" not in obj and "/archives/" not in obj

        check_no_url(cleaned)

    def test_backend_identity_set_creation_before_sanitization(self):
        """Verifies backend identity parsing works on raw results before persistence."""
        raw_results = [
            {
                "result_index": 1,
                "result_url": "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"
            }
        ]
        id_set, stats = mdr1r1.build_canonical_backend_identity_set(raw_results)
        assert ("C042WS25QAX", "1718079276.126469", "1718058683.912369") in id_set
        assert stats["backend_result_count"] == 1
        assert stats["backend_identity_parse_success_count"] == 1

    def test_deterministic_live_path_offline_integration(self, tmp_path, monkeypatch):
        """Tests the full live code path using simulated backend and host components offline.
        
        Specifically tests that report initialization -> harvest_search_telemetry -> safety evaluation ->
        DOM snapshot evaluate -> membership evaluation -> classification -> sanitized persistence executes
        flawlessly without KeyError or unhandled exception.
        """
        # Point paths to tmp_path
        fake_marker = tmp_path / "task11q4_p2a_l1_mdr1r1_attempt_started.json"
        fake_live = tmp_path / "task11q4_p2a_l1_mdr1r1_mounted_dom_residency.json"
        fake_out_dir = tmp_path
        monkeypatch.setattr(mdr1r1, "ATTEMPT_STARTED_MARKER_FILE", fake_marker)
        monkeypatch.setattr(mdr1r1, "LIVE_OUTPUT_FILE", fake_live)
        monkeypatch.setattr(mdr1r1, "OUTPUT_DIR", fake_out_dir)

        # Mock Host
        class FakeHost:
            def __init__(self, *args, **kwargs):
                self.destroyed = False

            def evaluate_js(self, script, timeout=10.0):
                # Return valid DOM snapshot with target at position 3
                return {
                    "ok": True,
                    "search_view_connected": True,
                    "mounted_row_count": 5,
                    "inspected_mounted_row_count": 5,
                    "connected_row_count": 5,
                    "positive_geometry_row_count": 5,
                    "target_match_count": 1,
                    "target_mounted_dom_position": 3,
                    "target_connected": True,
                    "target_positive_geometry": True,
                    "mounted_row_records": [
                        {
                            "mounted_dom_position": 3,
                            "channel_id": "C042WS25QAX",
                            "message_ts": "1718079276.126469",
                            "thread_ts": "1718058683.912369",
                            "connected": True,
                            "positive_geometry": True,
                            "identity_parse_success": True,
                            "is_thread_reply": True,
                            "is_target": True,
                        }
                    ]
                }

            def destroy(self):
                self.destroyed = True

        # Mock Backend
        class FakeBackend:
            def __init__(self, config=None, host=None):
                self.host = host
                self.last_search_telemetry = {
                    "safe_search_action_selected": True,
                    "generic_default_onenter_used": False,
                    "slackbot_action_invoked_by_pesu": False,
                    "write_capable_action_invoked_by_pesu": False,
                    "unsafe_non_search_action_invoked_by_pesu": False,
                    "verified_query_state_before_submit": True,
                    "SEARCH_RESULT_READINESS": "RESULTS_READY",
                    "forensic_transition": {
                        "query_submit_triggered": True,
                        "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
                        "SEARCH_SURFACE_QUERY_AFTER": "다음 결과: 26년 하반기 간담회",
                        "raw_result_identifiers": [
                            "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"
                        ]
                    },
                    "raw_items_evaluated": [
                        {
                            "raw_index": 1,
                            "canonical_permalink": "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369",
                            "qualifying_permalinks": [
                                "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"
                            ],
                            "sender_text": "이재희",
                            "normalized_body": "본문",
                            "body_valid": True,
                            "ambiguous_fragment_count": 0,
                            "thread_reply_detected": True,
                            "drop_reason": None,
                        }
                    ]
                }

            def start_runtime(self, timeout=30.0):
                pass

            def search_messages(self, query, max_results=5):
                return [
                    {
                        "result_index": 1,
                        "result_url": "https://heumlabs.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"
                    }
                ]

        monkeypatch.setattr(mdr1r1, "PesuWebViewHost", FakeHost)
        monkeypatch.setattr(mdr1r1, "WebViewSlackSearchBackend", FakeBackend)

        report = mdr1r1.run_mdr1r1_diagnostic(dry_run=False)

        assert report["verdict"] == "DIAGNOSTIC_COMPLETED"
        assert report["diagnostic_verdict"] == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"
        assert report["residency_classification"] == "TARGET_FOUND_IN_CONNECTED_MOUNTED_DOM"
        assert report["search_surface_current_query_correlated"] is True
        assert report["target_row_current_query_membership"] == "PROVEN"
        assert report["checks"]["search_safety_gate"] == "PASS"
        assert report["checks"]["atomic_dom_snapshot_executed"] is True
        assert fake_marker.exists()
        assert fake_live.exists()

        # Check persisted artifact is sanitized
        with open(fake_live, "r", encoding="utf-8") as f:
            persisted = json.load(f)

        assert persisted["verdict"] == "DIAGNOSTIC_COMPLETED"
        # Verify no URL in persisted artifact
        content_str = json.dumps(persisted)
        assert "http://" not in content_str
        assert "https://" not in content_str
        assert "/archives/" not in content_str

    def test_target_membership_cases_position_independent(self):
        """Verifies membership strictly reflects backend identity set, not DOM position."""
        target_tuple = mdr1r1.CANONICAL_TARGET_IDENTITY
        other_tuple = ("C042WS25QAX", "1788000000.000001", None)
        backend_set_with_target = {target_tuple, other_tuple}
        backend_set_without_target = {other_tuple}

        # Case 1: Target at position 3, in backend set -> PROVEN
        m1 = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=target_tuple,
            backend_identity_set=backend_set_with_target
        )
        assert m1 == "PROVEN"

        # Case 2: Target at position 6, in backend set -> PROVEN
        m2 = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=target_tuple,
            backend_identity_set=backend_set_with_target
        )
        assert m2 == "PROVEN"

        # Case 3: Target at position 6, absent from backend set -> UNPROVEN
        m3 = mdr1r1.evaluate_row_current_query_membership(
            row_connected=True,
            surface_correlated=True,
            row_identity=target_tuple,
            backend_identity_set=backend_set_without_target
        )
        assert m3 == "UNPROVEN"
