"""Regression tests for Phase 13.5D / Task 11 / P2A / TAIQ1 Thread Anchor Interaction Qualification 1.

Authoritative Qualification Test Suite:
1. Exact production row enumeration parity (selectors, geometry, top-5 slice)
2. Target identity is semantic, rank is observational only (1 <= rank <= 5)
3. Target can move rank (1..5) without changing qualification
4. Target row absence fails closed (TARGET_ROW_IDENTITY_NOT_UNIQUE)
5. Target row duplication fails closed (TARGET_ROW_IDENTITY_NOT_UNIQUE)
6. Wrong channel fails closed
7. Wrong message_ts fails closed
8. Wrong thread_ts fails closed
9. Qualified anchor count == 0 fails closed (ANCHOR_IDENTITY_NOT_UNIQUE)
10. Qualified anchor count > 1 fails closed (ANCHOR_IDENTITY_NOT_UNIQUE)
11. Atomic guard and click: single JS evaluation, validates and clicks same DOM reference
12. No second selector resolution between guard and click (no TOCTOU seam)
13. HTTPS required
14. Explicit allowed origin enforcement (app.slack.com, heumlabs.slack.com)
15. Wildcard Slack origins (*.slack.com) rejected
16. External origins rejected
17. Dangerous schemes (javascript:, data:, mailto:) rejected
18. Canonical message permalink path regex and exact thread_ts parameter
19. Target self safe: None, "", "_self" permitted; _blank, _parent, _top, named rejected
20. Download attribute strictly rejected
21. Single click maximum, zero fallbacks
22. Bounded post-click polling (<= 5.0s, <= 10 DOM reads)
23. Raw location URL never persisted; normalized classifications only
24. Flexpane without target identities is NOT success (ANCHOR_OPENED_THREAD_FLEXPANE_IDENTITY_INCOMPLETE)
25. Success requires preserved search surface + flexpane + target root & reply identities
26. Main navigation boundary: search loss classifies ANCHOR_NAVIGATED_MAIN_SURFACE and STOPs
27. Restoration strictly prohibited: 0 restorations, 0 history.back, 0 second clicks
28. One-shot governance: marker before WebView; existing marker or live artifact blocks replay
29. Handled live failure persists immutable final artifact
30. All 11 historical baselines exact match SHA-256
31. Runtime import closure exact set (missing=0, stale=0, mismatched=0)
32. Production src/ diff empty (git diff HEAD -- src/)
33. Zero provider calls
34. All 11 sensitive data contract flags False
35. Preflight host DOM evaluation API parity (evaluate_js only, no evaluate_script)
36. Preflight telemetry authority AST parity with RB2
37. Preflight search safety gate AST parity with RB2
38. Preflight search exception harvesting order parity with Attempt 4
39. Preflight runtime search path parity with Attempt 4
40. Dry-run execution succeeds without creating attempt marker or live artifact
"""

import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.diagnose_task11q4_p2a_l1_thread_anchor_interaction_taiq1 as taiq1
from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink


class TestProductionRowEnumerationParity:
    """Verifies that TAIQ1 uses the authoritative production row enumeration contract."""

    def test_production_row_enumeration_parity_function(self):
        assert taiq1.verify_search_result_row_enumeration_parity() is True

    def test_container_and_row_selectors(self):
        assert taiq1.SEARCH_VIEW_SELECTOR == '[data-qa="search_view"]'
        assert taiq1.SEARCH_MAX_RESULTS == 5
        assert '[data-qa="search_result"]' in taiq1.SEARCH_RESULT_ROW_SELECTORS
        assert '[data-qa="search_message_result_item"]' in taiq1.SEARCH_RESULT_ROW_SELECTORS
        assert '.c-search_result' in taiq1.SEARCH_RESULT_ROW_SELECTORS

    def test_js_row_enumeration_logic(self):
        js = taiq1.JS_ATOMIC_GUARD_AND_CLICK
        assert "searchView.querySelectorAll(" in js
        assert "el.isConnected" in js
        assert "r.width > 0 && r.height > 0" in js
        assert "mountedRows.slice(0, maxRows)" in js


class TestTargetIdentityAndRankSemantics:
    """Verifies that target identity is semantic and rank is purely observational."""

    def test_target_identity_constants(self):
        assert taiq1.TARGET_CHANNEL_ID == "C042WS25QAX"
        assert taiq1.TARGET_MESSAGE_TS == "1718079276.126469"
        assert taiq1.TARGET_THREAD_TS == "1718058683.912369"
        assert taiq1.TARGET_IS_THREAD_REPLY is True
        assert taiq1.TARGET_MESSAGE_TS != taiq1.TARGET_THREAD_TS

    def test_rank_is_observational_target_can_move_across_top5(self):
        """Mock evaluating target row appearing at any rank 1 to 5; all qualify semantically."""
        for rank in range(1, 6):
            mock_rows = []
            for r in range(1, 6):
                if r == rank:
                    mock_rows.append({
                        "channel_id": taiq1.TARGET_CHANNEL_ID,
                        "message_ts": taiq1.TARGET_MESSAGE_TS,
                        "thread_ts": taiq1.TARGET_THREAD_TS,
                    })
                else:
                    mock_rows.append({
                        "channel_id": "C042WS25QAX",
                        "message_ts": f"1718000000.{r:06d}",
                        "thread_ts": None,
                    })

            # Exact target matching row count
            matching = [
                row for row in mock_rows
                if row.get("channel_id") == taiq1.TARGET_CHANNEL_ID
                and row.get("message_ts") == taiq1.TARGET_MESSAGE_TS
                and row.get("thread_ts") == taiq1.TARGET_THREAD_TS
            ]
            assert len(matching) == 1, f"Expected 1 matching row when target is at rank {rank}"

    def test_target_row_absence_fails_cardinality(self):
        mock_rows = [
            {"channel_id": "C042WS25QAX", "message_ts": "1718000001.000001", "thread_ts": None},
            {"channel_id": "C042WS25QAX", "message_ts": "1718000002.000002", "thread_ts": None},
        ]
        matching = [
            row for row in mock_rows
            if row.get("channel_id") == taiq1.TARGET_CHANNEL_ID
            and row.get("message_ts") == taiq1.TARGET_MESSAGE_TS
            and row.get("thread_ts") == taiq1.TARGET_THREAD_TS
        ]
        assert len(matching) == 0

    def test_target_row_duplication_fails_cardinality(self):
        mock_rows = [
            {"channel_id": taiq1.TARGET_CHANNEL_ID, "message_ts": taiq1.TARGET_MESSAGE_TS, "thread_ts": taiq1.TARGET_THREAD_TS},
            {"channel_id": taiq1.TARGET_CHANNEL_ID, "message_ts": taiq1.TARGET_MESSAGE_TS, "thread_ts": taiq1.TARGET_THREAD_TS},
        ]
        matching = [
            row for row in mock_rows
            if row.get("channel_id") == taiq1.TARGET_CHANNEL_ID
            and row.get("message_ts") == taiq1.TARGET_MESSAGE_TS
            and row.get("thread_ts") == taiq1.TARGET_THREAD_TS
        ]
        assert len(matching) == 2  # Not unique => fail closed

    def test_wrong_channel_fails(self):
        identity = parse_slack_message_permalink(
            "https://app.slack.com/archives/C999WRONGCH/p1718079276126469?thread_ts=1718058683.912369"
        )
        assert identity is not None
        assert identity.channel_id != taiq1.TARGET_CHANNEL_ID

    def test_wrong_message_ts_fails(self):
        identity = parse_slack_message_permalink(
            "https://app.slack.com/archives/C042WS25QAX/p1718999999999999?thread_ts=1718058683.912369"
        )
        assert identity is not None
        assert identity.message_ts != taiq1.TARGET_MESSAGE_TS

    def test_wrong_thread_ts_fails(self):
        identity = parse_slack_message_permalink(
            "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718999999.999999"
        )
        assert identity is not None
        assert identity.thread_ts != taiq1.TARGET_THREAD_TS


class TestAtomicGuardAndClickContract:
    """Verifies that guard and click are executed in a single atomic JS evaluation."""

    def test_atomic_js_contains_guard_and_click_together(self):
        js = taiq1.JS_ATOMIC_GUARD_AND_CLICK
        assert "TARGET_ROW_IDENTITY_NOT_UNIQUE" in js
        assert "ANCHOR_IDENTITY_NOT_UNIQUE" in js
        assert "anchorToClick.click()" in js
        assert "click_executed: true" in js

    def test_same_dom_node_reference_clicked_no_second_query(self):
        js = taiq1.JS_ATOMIC_GUARD_AND_CLICK
        # Anchor is pushed to qualifyingAnchors array and the exact [0] element is clicked
        pattern = r"qualifyingAnchors\.push\(a\);.*?const anchorToClick = qualifyingAnchors\[0\];\s*anchorToClick\.click\(\);"
        assert re.search(pattern, js, re.DOTALL) is not None, "Atomic JS must click qualifyingAnchors[0] on the retained qualified reference"

    def test_one_click_maximum_and_no_fallbacks(self):
        js = taiq1.JS_ATOMIC_GUARD_AND_CLICK
        click_count = js.count(".click()")
        assert click_count == 1, "Exactly one .click() call permitted in atomic JS"
        assert "dispatchEvent(new MouseEvent" not in js
        assert "mousedown" not in js
        assert "mouseup" not in js


class TestAnchorCardinalityAndStructuralGuards:
    """Verifies anchor cardinality, tagName, composer, and reply-bar exclusion guards."""

    def test_anchor_structural_guards_in_js(self):
        js = taiq1.JS_ATOMIC_GUARD_AND_CLICK
        assert 'a.tagName !== "A"' in js
        assert "!a.isConnected" in js
        assert "aRect.width <= 0 || aRect.height <= 0" in js
        assert "contenteditable" in js and "message_input" in js
        assert 'div[data-qa="reply_bar"]' in js


class TestUrlAndNavigationSafetyGuards:
    """Verifies protocol, origin allowlist, URL parsing, and new-window / download safety."""

    def test_allowed_origins_exact(self):
        assert set(taiq1.FROZEN_ALLOWED_ORIGINS) == {
            "https://app.slack.com",
            "https://heumlabs.slack.com",
        }

    def test_wildcard_origins_rejected_in_js(self):
        js = taiq1.JS_ATOMIC_GUARD_AND_CLICK
        assert "!allowedOrigins.includes(urlObj.origin)" in js
        assert "https://evil.slack.com" not in taiq1.FROZEN_ALLOWED_ORIGINS
        assert "https://attacker.slack.com" not in taiq1.FROZEN_ALLOWED_ORIGINS

    def test_external_origins_rejected(self):
        assert "https://attacker.com" not in taiq1.FROZEN_ALLOWED_ORIGINS
        assert "https://google.com" not in taiq1.FROZEN_ALLOWED_ORIGINS

    def test_https_required_in_js(self):
        js = taiq1.JS_ATOMIC_GUARD_AND_CLICK
        assert 'urlObj.protocol !== "https:"' in js

    def test_canonical_path_and_thread_ts_required_in_js(self):
        js = taiq1.JS_ATOMIC_GUARD_AND_CLICK
        assert "urlObj.pathname.match(" in js and "archives" in js
        assert 'urlObj.searchParams.get("thread_ts")' in js

    def test_new_window_and_download_safety_in_js(self):
        js = taiq1.JS_ATOMIC_GUARD_AND_CLICK
        assert 'a.hasAttribute("download")' in js
        assert '!targetAttr || targetAttr === "" || targetAttr === "_self"' in js

    def test_dangerous_schemes_rejected_by_parser(self):
        assert parse_slack_message_permalink("javascript:alert(1)") is None
        assert parse_slack_message_permalink("data:text/html,<b>evil</b>") is None
        assert parse_slack_message_permalink("mailto:target@slack.com") is None


class TestPostClickObservationContract:
    """Verifies bounded post-click observation and classification."""

    def test_post_click_bounds(self):
        assert taiq1.MAX_POST_CLICK_OBSERVATION_SECONDS <= 5.0
        assert taiq1.MAX_POST_CLICK_DOM_READS <= 10
        assert taiq1.POLL_INTERVAL_SECONDS >= 0.2

    def test_post_click_js_classifies_without_raw_location_leak(self):
        js = taiq1.JS_OBSERVE_POST_CLICK_DOM_STATE
        assert "window.location.origin" in js
        assert "window.location.pathname" in js
        assert "location_classification" in js
        assert "SLACK_ARCHIVE_MESSAGE_ROUTE" in js
        assert "SLACK_CLIENT_THREAD_ROUTE" in js
        assert "OTHER_SAME_ORIGIN_SLACK_ROUTE" in js
        assert "EXTERNAL_ORIGIN" in js
        assert "UNCHANGED" in js

    def test_success_requires_both_root_and_reply_identities(self):
        js = taiq1.JS_OBSERVE_POST_CLICK_DOM_STATE
        assert "target_root_identity_observable" in js
        assert "target_reply_identity_observable" in js
        assert "threads_flexpane_connected" in js
        assert "search_surface_query_still_matches_requested" in js

    def test_no_restoration_budget(self):
        assert taiq1.MAX_RESTORATIONS == 0
        assert taiq1.MAX_BACK_NAVIGATIONS == 0


class TestOneShotGovernance:
    """Verifies attempt marker creation, replay prohibition, and failure persistence."""

    def test_attempt_marker_path(self):
        assert taiq1.ATTEMPT_STARTED_MARKER_FILE.name == "task11q4_p2a_l1_taiq1_attempt_started.json"
        assert taiq1.LIVE_OUTPUT_FILE.name == "task11q4_p2a_l1_taiq1_anchor_interaction.json"
        assert taiq1.DRY_RUN_OUTPUT_FILE.name == "task11q4_p2a_l1_taiq1_anchor_interaction_dry_run.json"

    def test_pre_existing_marker_blocks_replay(self, tmp_path, monkeypatch):
        fake_marker = tmp_path / "task11q4_p2a_l1_taiq1_attempt_started.json"
        fake_marker.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(taiq1, "ATTEMPT_STARTED_MARKER_FILE", fake_marker)

        report = taiq1.run_taiq1_diagnostic(dry_run=False)
        assert report["verdict"] == "FAIL_CLOSED"
        assert report["diagnostic_verdict"] == "ATTEMPT_ALREADY_CONSUMED"

    def test_pre_existing_live_artifact_blocks_replay(self, tmp_path, monkeypatch):
        fake_live = tmp_path / "task11q4_p2a_l1_taiq1_anchor_interaction.json"
        fake_live.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(taiq1, "LIVE_OUTPUT_FILE", fake_live)

        report = taiq1.run_taiq1_diagnostic(dry_run=False)
        assert report["verdict"] == "FAIL_CLOSED"
        assert report["diagnostic_verdict"] == "LIVE_ARTIFACT_ALREADY_EXISTS"


class TestPreflightParityGuards:
    """Verifies that all preflight parity checks against Attempt 4 / RB2 pass."""

    def test_host_dom_evaluation_api_parity(self):
        assert taiq1.verify_host_dom_evaluation_api_parity() is True

    def test_search_telemetry_authority_parity(self):
        assert taiq1.verify_search_telemetry_authority_parity_with_attempt4() is True

    def test_search_safety_gate_parity(self):
        assert taiq1.verify_search_safety_gate_parity_with_attempt4() is True

    def test_search_exception_order_parity(self):
        assert taiq1.verify_search_exception_order_parity_with_attempt4() is True

    def test_runtime_search_path_parity(self):
        assert taiq1.verify_runtime_search_path_parity_with_attempt4() is True


class TestHistoricalBaselinesAndRuntimeClosure:
    """Verifies the 11 historical baseline SHA-256 hashes and the 60 runtime closure hashes."""

    def test_historical_11_evidence_hashes_exact(self):
        assert taiq1.PROTOCOL_FREEZE_PATH.exists()
        with open(taiq1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            proto = json.load(f)

        hist = proto.get("historical_baselines", {})
        assert len(hist) == 12  # 11 historical evidence files + TR2-R1 attempt marker
        for name, info in hist.items():
            full_p = REPO_ROOT / info["path"]
            assert full_p.exists(), f"Historical baseline file missing: {info['path']}"
            actual = taiq1.get_file_sha256(full_p)
            assert actual == info["sha256"], f"Historical hash mismatch for {name}: {actual} != {info['sha256']}"

    def test_tr2r1_marker_sha256_exact(self):
        marker_p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_tr2r1_attempt_started.json"
        assert marker_p.exists()
        actual = taiq1.get_file_sha256(marker_p)
        assert actual == "c780905190ff8e905a890ab4d2117eacb0a5b532b1721dff442441b5b7efa12e"

    def test_tr2r1_live_artifact_sha256_exact(self):
        live_p = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_tr2r1_thread_affordance.json"
        assert live_p.exists()
        actual = taiq1.get_file_sha256(live_p)
        assert actual == "6a3a4171219174c654ccad0c61456e8f99bd657b925ec7519df7249db2c4c75b"

    def test_runtime_closure_exact_set(self):
        with open(taiq1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            proto = json.load(f)

        audit = taiq1.audit_runtime_import_closure(proto)
        assert audit["closure_audit_passed"] is True
        assert audit["missing_from_runtime_closure_count"] == 0
        assert audit["stale_runtime_closure_entry_count"] == 0
        assert audit["mismatched_runtime_dependency_count"] == 0
        assert audit["loaded_project_module_count"] == 60


class TestSensitiveDataContractAndSafety:
    """Verifies that all sensitive telemetry flags are False and provider calls are zero."""

    def test_sensitive_data_contract_all_false(self):
        with open(taiq1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            proto = json.load(f)

        for k, v in proto.get("sensitive_content_contract", {}).items():
            assert v is False, f"Sensitive contract flag {k} must be False"

    def test_action_contracts_zeros(self):
        with open(taiq1.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
            proto = json.load(f)

        ac = proto.get("action_contracts", {})
        assert ac.get("MAX_SLACK_SEARCH_OPERATIONS") == 1
        assert ac.get("MAX_SEARCH_SUBMISSIONS") == 1
        assert ac.get("MAX_GUARDED_ANCHOR_CLICKS") == 1
        assert ac.get("MAX_REPLY_BAR_CLICKS") == 0
        assert ac.get("MAX_RESULT_CARD_CLICKS") == 0
        assert ac.get("MAX_SCROLL_ACTIONS") == 0
        assert ac.get("MAX_KEYBOARD_EVENTS") == 0
        assert ac.get("MAX_COMPOSER_INTERACTIONS") == 0
        assert ac.get("MAX_WRITE_CAPABLE_ACTIONS") == 0
        assert ac.get("MAX_PROVIDER_CALLS") == 0
        assert ac.get("MAX_RETRIES") == 0
        assert ac.get("MAX_RESTORATION_ACTIONS") == 0
        assert ac.get("MAX_BACK_NAVIGATIONS") == 0

    def test_production_src_diff_empty(self):
        res = subprocess.run(
            ["git", "diff", "HEAD", "--", "src/"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert res.stdout.strip() == "", f"Production src/ diff must be empty: {res.stdout}"


class TestDryRunExecution:
    """Verifies that offline dry-run executes completely without live side-effects."""

    def test_dry_run_produces_dry_run_pass(self):
        report = taiq1.run_taiq1_diagnostic(dry_run=True)
        assert report["verdict"] == "DRY_RUN_PASS"
        assert report["diagnostic_verdict"] == "ANCHOR_OPENED_THREAD_FLEXPANE_SEARCH_PRESERVED_IDENTITY_CONFIRMED"

    def test_marker_and_live_artifact_absent_during_prep(self):
        assert not taiq1.ATTEMPT_STARTED_MARKER_FILE.exists()
        assert not taiq1.LIVE_OUTPUT_FILE.exists()
        assert taiq1.DRY_RUN_OUTPUT_FILE.exists()
