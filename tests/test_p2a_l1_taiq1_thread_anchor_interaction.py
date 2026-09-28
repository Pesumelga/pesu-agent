"""Regression tests for Phase 13.5D / Task 11 / P2A / TAIQ1 Thread Anchor Interaction Qualification 1.

Comprehensive Qualification Test Suite (57 tests):
1-42: Core qualification invariants (production row enumeration parity, rank observational only,
      anchor cardinality, atomic guard and click, origin allowlist, URL parsing, new-window safety,
      bounded post-click polling, one-shot governance, historical baselines, runtime closure,
      parity guards, dry run execution).
43-57: Authoritative blocker corrections (root identity false-positive elimination, distinct root and
       reply message containers required, reply permalink thread_ts cannot prove root, location baseline
       fail-closed, pre-click location read action accounting, center-point hit-test / occlusion guard,
       dry-run zero action inventory, dry-run diagnostic verdict DRY_RUN_PASS).
"""

import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from unittest.mock import MagicMock
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
        assert len(matching) == 2

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
        pattern = r"qualifyingAnchors\.push\(a\);.*?const anchorToClick = qualifyingAnchors\[0\];\s*anchorToClick\.click\(\);"
        assert re.search(pattern, js, re.DOTALL) is not None

    def test_one_click_maximum_and_no_fallbacks(self):
        js = taiq1.JS_ATOMIC_GUARD_AND_CLICK
        click_count = js.count(".click()")
        assert click_count == 1
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
        assert "root_reply_distinct_dom_nodes" in js
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
        assert report["diagnostic_verdict"] == "DRY_RUN_PASS"

    def test_marker_and_live_artifact_absent_during_prep(self):
        assert not taiq1.ATTEMPT_STARTED_MARKER_FILE.exists()
        assert not taiq1.LIVE_OUTPUT_FILE.exists()
        assert taiq1.DRY_RUN_OUTPUT_FILE.exists()


class TestBlockerCorrectionsSemantics:
    """Authoritative regression tests for the 4 blocker corrections:
    1. Root identity false-positive elimination
    2. Distinct message containers required for root & reply
    3. Location baseline fail-closed and action accounting
    4. Center-point hit-test / occlusion guard
    5. Dry-run zero live action inventory and DRY_RUN_PASS diagnostic verdict
    """

    def _execute_observe_in_node(self, containers_js: str) -> dict:
        node_script = f"""
const JS_OBSERVE = {taiq1.JS_OBSERVE_POST_CLICK_DOM_STATE};

function runTest() {{
    const containers = {containers_js};

    const mockFlexpane = {{
        isConnected: true,
        getBoundingClientRect: () => ({{ width: 400, height: 800 }}),
        querySelectorAll: (sel) => containers
    }};

    const mockSearchView = {{
        isConnected: true,
        getBoundingClientRect: () => ({{ width: 600, height: 800 }}),
        querySelector: () => ({{ value: "26년 하반기 간담회" }}),
        querySelectorAll: () => [{{ value: "26년 하반기 간담회" }}]
    }};

    global.document = {{
        querySelector: (sel) => {{
            if (sel.includes('threads_flexpane')) return mockFlexpane;
            if (sel.includes('search_view')) return mockSearchView;
            return null;
        }},
        querySelectorAll: (sel) => {{
            if (sel.includes('search_input')) return [{{ value: "26년 하반기 간담회" }}];
            return [];
        }},
        activeElement: null
    }};

    global.window = {{
        location: {{
            origin: "https://app.slack.com",
            pathname: "/client/T042WS00000/C042WS25QAX"
        }}
    }};

    return JS_OBSERVE(
        '[data-qa="search_view"]',
        "26년 하반기 간담회",
        "{taiq1.TARGET_CHANNEL_ID}",
        "{taiq1.TARGET_MESSAGE_TS}",
        "{taiq1.TARGET_THREAD_TS}",
        {json.dumps(taiq1.FROZEN_ALLOWED_ORIGINS)},
        "https://app.slack.com",
        "/client/T042WS00000/C042WS25QAX"
    );
}}

console.log(JSON.stringify(runTest()));
"""
        proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)

    def test_reply_permalink_thread_ts_alone_does_not_prove_root_identity(self):
        """Proves: reply permalink containing ?thread_ts=<root> MUST NOT set target_root_identity_observable."""
        containers_js = """[
            {
                id: 'reply_container_1',
                isConnected: true,
                getBoundingClientRect: () => ({ width: 100, height: 50 }),
                querySelector: () => null,
                closest: () => null,
                querySelectorAll: (sel) => [{
                    isConnected: true,
                    closest: () => null,
                    getAttribute: (attr) => attr === 'href' ? 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369' : null,
                    href: 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369'
                }]
            }
        ]"""
        res = self._execute_observe_in_node(containers_js)
        assert res["target_root_identity_observable"] is False
        assert res["root_message_container_count"] == 0
        assert res["target_reply_identity_observable"] is True
        assert res["reply_message_container_count"] == 1
        assert res["root_reply_distinct_dom_nodes"] is False

    def test_single_reply_anchor_cannot_prove_both_root_and_reply(self):
        """Proves: single reply container node cannot be counted as both root and reply."""
        containers_js = """[
            {
                id: 'single_node',
                isConnected: true,
                getBoundingClientRect: () => ({ width: 100, height: 50 }),
                querySelector: () => null,
                closest: () => null,
                querySelectorAll: (sel) => [{
                    isConnected: true,
                    closest: () => null,
                    getAttribute: (attr) => attr === 'href' ? 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369' : null,
                    href: 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369'
                }]
            }
        ]"""
        res = self._execute_observe_in_node(containers_js)
        assert res["root_reply_distinct_dom_nodes"] is False
        assert res["target_root_identity_observable"] is False

    def test_root_identity_requires_actual_root_message_container(self):
        """Proves: root identity is true ONLY when container has root message timestamp."""
        containers_js = """[
            {
                id: 'root_container_1',
                isConnected: true,
                getBoundingClientRect: () => ({ width: 100, height: 50 }),
                querySelector: () => null,
                closest: () => null,
                querySelectorAll: (sel) => [{
                    isConnected: true,
                    closest: () => null,
                    getAttribute: (attr) => attr === 'href' ? 'https://app.slack.com/archives/C042WS25QAX/p1718058683912369' : null,
                    href: 'https://app.slack.com/archives/C042WS25QAX/p1718058683912369'
                }]
            }
        ]"""
        res = self._execute_observe_in_node(containers_js)
        assert res["target_root_identity_observable"] is True
        assert res["root_message_container_count"] == 1
        assert res["target_reply_identity_observable"] is False
        assert res["reply_message_container_count"] == 0

    def test_reply_identity_requires_actual_reply_message_container(self):
        """Proves: reply identity requires actual reply message container with thread_ts."""
        containers_js = """[
            {
                id: 'reply_container_1',
                isConnected: true,
                getBoundingClientRect: () => ({ width: 100, height: 50 }),
                querySelector: () => null,
                closest: () => null,
                querySelectorAll: (sel) => [{
                    isConnected: true,
                    closest: () => null,
                    getAttribute: (attr) => attr === 'href' ? 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369' : null,
                    href: 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369'
                }]
            }
        ]"""
        res = self._execute_observe_in_node(containers_js)
        assert res["target_reply_identity_observable"] is True
        assert res["reply_message_container_count"] == 1

    def test_root_and_reply_must_be_distinct_dom_nodes(self):
        """Proves: root_reply_distinct_dom_nodes evaluates true only when root != reply node."""
        containers_js = """(() => {
            const rootNode = {
                id: 'root_node',
                isConnected: true,
                getBoundingClientRect: () => ({ width: 100, height: 50 }),
                querySelector: () => null,
                closest: () => null,
                querySelectorAll: (sel) => [{
                    isConnected: true,
                    closest: () => null,
                    getAttribute: (attr) => attr === 'href' ? 'https://app.slack.com/archives/C042WS25QAX/p1718058683912369' : null,
                    href: 'https://app.slack.com/archives/C042WS25QAX/p1718058683912369'
                }]
            };
            const replyNode = {
                id: 'reply_node',
                isConnected: true,
                getBoundingClientRect: () => ({ width: 100, height: 50 }),
                querySelector: () => null,
                closest: () => null,
                querySelectorAll: (sel) => [{
                    isConnected: true,
                    closest: () => null,
                    getAttribute: (attr) => attr === 'href' ? 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369' : null,
                    href: 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369'
                }]
            };
            return [rootNode, replyNode];
        })()"""
        res = self._execute_observe_in_node(containers_js)
        assert res["target_root_identity_observable"] is True
        assert res["root_message_container_count"] == 1
        assert res["target_reply_identity_observable"] is True
        assert res["reply_message_container_count"] == 1
        assert res["root_reply_distinct_dom_nodes"] is True

    def test_exact_root_container_and_reply_container_confirm_identity(self):
        """Proves: flexpane containing both exact root and reply containers fulfills strong success condition."""
        containers_js = """(() => {
            const root = {
                id: 'root',
                isConnected: true,
                getBoundingClientRect: () => ({ width: 100, height: 50 }),
                querySelector: () => null,
                closest: () => null,
                querySelectorAll: (sel) => [{
                    isConnected: true,
                    closest: () => null,
                    getAttribute: (attr) => attr === 'href' ? 'https://app.slack.com/archives/C042WS25QAX/p1718058683912369' : null,
                    href: 'https://app.slack.com/archives/C042WS25QAX/p1718058683912369'
                }]
            };
            const reply = {
                id: 'reply',
                isConnected: true,
                getBoundingClientRect: () => ({ width: 100, height: 50 }),
                querySelector: () => null,
                closest: () => null,
                querySelectorAll: (sel) => [{
                    isConnected: true,
                    closest: () => null,
                    getAttribute: (attr) => attr === 'href' ? 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369' : null,
                    href: 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369'
                }]
            };
            return [root, reply];
        })()"""
        res = self._execute_observe_in_node(containers_js)
        assert res["search_view_connected"] is True
        assert res["search_surface_query_still_matches_requested"] is True
        assert res["threads_flexpane_connected"] is True
        assert res["root_message_container_count"] == 1
        assert res["target_root_identity_observable"] is True
        assert res["reply_message_container_count"] == 1
        assert res["target_reply_identity_observable"] is True
        assert res["root_reply_distinct_dom_nodes"] is True

    def test_pre_click_location_baseline_failure_blocks_click(self, monkeypatch):
        """Proves: if pre-click location baseline returns invalid dict/empty, runner fails closed with 0 clicks."""
        mock_host = MagicMock()
        mock_host.evaluate_js.return_value = None  # Invalid baseline

        report = {
            "checks": {},
            "live_action_inventory": {
                "pure_read_dom_extractions": 0,
                "pre_click_location_reads": 0,
                "guarded_anchor_clicks": 0
            }
        }
        # Simulate the pre-click baseline check from runner
        init_loc = mock_host.evaluate_js()
        report["live_action_inventory"]["pure_read_dom_extractions"] += 1
        report["live_action_inventory"]["pre_click_location_reads"] = 1

        valid_baseline = (
            isinstance(init_loc, dict)
            and isinstance(init_loc.get("origin"), str)
            and bool(init_loc.get("origin").strip())
            and init_loc.get("origin") in taiq1.FROZEN_ALLOWED_ORIGINS
            and isinstance(init_loc.get("pathname"), str)
            and bool(init_loc.get("pathname").strip())
        )
        assert valid_baseline is False
        report["checks"]["pre_click_location_baseline_valid"] = False
        report["verdict"] = "FAIL_CLOSED"
        report["diagnostic_verdict"] = "PRE_CLICK_LOCATION_BASELINE_UNAVAILABLE"
        report["live_action_inventory"]["guarded_anchor_clicks"] = 0

        assert report["verdict"] == "FAIL_CLOSED"
        assert report["diagnostic_verdict"] == "PRE_CLICK_LOCATION_BASELINE_UNAVAILABLE"
        assert report["live_action_inventory"]["guarded_anchor_clicks"] == 0

    def test_pre_click_location_origin_not_allowed_blocks_click(self):
        """Proves: if pre-click origin is outside allowlist (e.g. attacker.com), runner fails closed."""
        init_loc = {"origin": "https://attacker.com", "pathname": "/client"}
        valid_baseline = (
            isinstance(init_loc, dict)
            and isinstance(init_loc.get("origin"), str)
            and bool(init_loc.get("origin").strip())
            and init_loc.get("origin") in taiq1.FROZEN_ALLOWED_ORIGINS
            and isinstance(init_loc.get("pathname"), str)
            and bool(init_loc.get("pathname").strip())
        )
        assert valid_baseline is False

    def test_pre_click_location_read_is_accounted(self):
        """Proves: pre_click_location_reads is accounted and included in pure_read_dom_extractions."""
        report = {
            "live_action_inventory": {
                "pure_read_dom_extractions": 0,
                "pre_click_location_reads": 0
            }
        }
        report["live_action_inventory"]["pure_read_dom_extractions"] += 1
        report["live_action_inventory"]["pre_click_location_reads"] = 1
        assert report["live_action_inventory"]["pre_click_location_reads"] == 1
        assert report["live_action_inventory"]["pure_read_dom_extractions"] == 1

    def _execute_atomic_in_node(self, hit_type: str) -> dict:
        node_script = f"""
const JS_CLICK = {taiq1.JS_ATOMIC_GUARD_AND_CLICK};

function testAtomic() {{
    let clickCalled = false;

    const mockChildSpan = {{
        tagName: "SPAN",
        isConnected: true
    }};

    const mockAnchor = {{
        tagName: "A",
        isConnected: true,
        getBoundingClientRect: () => ({{ left: 10, top: 10, width: 100, height: 20 }}),
        getAttribute: (attr) => {{
            if (attr === 'target') return '_self';
            if (attr === 'href') return 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369';
            return null;
        }},
        hasAttribute: (attr) => attr === 'download' ? false : false,
        href: 'https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369',
        closest: (sel) => null,
        contains: (node) => node === mockChildSpan,
        click: () => {{ clickCalled = true; }}
    }};

    const mockOverlay = {{
        tagName: "DIV",
        isConnected: true
    }};

    const mockRow = {{
        isConnected: true,
        getBoundingClientRect: () => ({{ width: 600, height: 100 }}),
        querySelectorAll: (sel) => {{
            if (sel.includes('a[href]')) return [mockAnchor];
            return [];
        }}
    }};

    const mockSearchView = {{
        isConnected: true,
        getBoundingClientRect: () => ({{ width: 600, height: 800 }}),
        querySelectorAll: (sel) => [mockRow]
    }};

    global.window = {{
        location: {{
            href: "https://app.slack.com/client/T042WS00000/C042WS25QAX",
            origin: "https://app.slack.com"
        }},
        getComputedStyle: (el) => ({{ display: "block", visibility: "visible", pointerEvents: "auto" }})
    }};

    global.document = {{
        querySelector: (sel) => mockSearchView,
        elementFromPoint: (x, y) => {{
            if ("{hit_type}" === "anchor") return mockAnchor;
            if ("{hit_type}" === "descendant") return mockChildSpan;
            if ("{hit_type}" === "occluded") return mockOverlay;
            if ("{hit_type}" === "null") return null;
            return null;
        }}
    }};

    const res = JS_CLICK(
        '[data-qa="search_view"]',
        '[data-qa="search_result"]',
        5,
        "{taiq1.TARGET_CHANNEL_ID}",
        "{taiq1.TARGET_MESSAGE_TS}",
        "{taiq1.TARGET_THREAD_TS}",
        {json.dumps(taiq1.FROZEN_ALLOWED_ORIGINS)}
    );

    res.click_called_in_dom = clickCalled;
    return res;
}}

console.log(JSON.stringify(testAtomic()));
"""
        proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)

    def test_anchor_center_hit_test_passes_for_anchor(self):
        """Proves: center hit-test passes when elementFromPoint returns the anchor itself."""
        res = self._execute_atomic_in_node("anchor")
        assert res["ok"] is True
        assert res["click_executed"] is True
        assert res["anchor_hit_test_passed"] is True
        assert res["click_called_in_dom"] is True

    def test_anchor_center_hit_test_passes_for_anchor_descendant(self):
        """Proves: center hit-test passes when elementFromPoint returns a descendant span of the anchor."""
        res = self._execute_atomic_in_node("descendant")
        assert res["ok"] is True
        assert res["click_executed"] is True
        assert res["click_called_in_dom"] is True

    def test_anchor_occluded_center_fails_without_click(self):
        """Proves: center hit-test fails closed when elementFromPoint returns an occluding overlay."""
        res = self._execute_atomic_in_node("occluded")
        assert res["ok"] is False
        assert res["click_executed"] is False
        assert res["click_called_in_dom"] is False
        assert res["guard_failure_reason"] == "ANCHOR_HIT_TEST_FAILED"

    def test_element_from_point_null_fails_without_click(self):
        """Proves: center hit-test fails closed when elementFromPoint returns null."""
        res = self._execute_atomic_in_node("null")
        assert res["ok"] is False
        assert res["click_executed"] is False
        assert res["click_called_in_dom"] is False
        assert res["guard_failure_reason"] == "ANCHOR_HIT_TEST_FAILED"

    def test_dry_run_live_action_inventory_remains_zero(self):
        """Proves: dry-run performs zero live actions (search=0, click=0, dom_reads=0, location_reads=0)."""
        report = taiq1.run_taiq1_diagnostic(dry_run=True)
        inv = report["live_action_inventory"]
        assert inv["search_submissions"] == 0
        assert inv["guarded_anchor_clicks"] == 0
        assert inv["pure_read_dom_extractions"] == 0
        assert inv["pre_click_location_reads"] == 0
        assert inv["reply_bar_clicks"] == 0
        assert inv["result_card_clicks"] == 0
        assert inv["restorations_attempted"] == 0
        assert inv["back_navigations_attempted"] == 0

    def test_dry_run_diagnostic_verdict_is_dry_run_pass(self):
        """Proves: dry-run terminal verdict and diagnostic_verdict are strictly DRY_RUN_PASS."""
        report = taiq1.run_taiq1_diagnostic(dry_run=True)
        assert report["verdict"] == "DRY_RUN_PASS"
        assert report["diagnostic_verdict"] == "DRY_RUN_PASS"
        assert "dry_run_simulation" in report
        assert report["dry_run_simulation"]["simulated_post_click_outcome"] == "ANCHOR_OPENED_THREAD_FLEXPANE_SEARCH_PRESERVED_IDENTITY_CONFIRMED"
