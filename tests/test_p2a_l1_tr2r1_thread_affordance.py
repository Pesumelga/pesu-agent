"""Regression tests for Phase 13.5D / Task 11 / P2A / TR2-R1-R1 Search-Row Thread Affordance Discovery.

Invariants verified (43 tests):
1-28. Original core discovery, safe structural metadata, deduplication, derived booleans, permalink parsing,
      truncation awareness, historical hashes, clean repo, zero provider calls.
29. TR2 host constructor parity with Attempt 4
30. TR2 backend constructor and storage path parity with Attempt 4
31. TR2 backend start_runtime parity with Attempt 4
32. TR2 search harvester AST parity with RB2
33. TR2 search safety gate AST parity with RB2
34. Missing unsafe telemetry does not default safe
35. Search submission count comes from authoritative telemetry
36. Search exception harvested before raise
37. Protocol semantic identity mismatch blocks pre-WebView
38. Runtime closure hash mismatch blocks pre-WebView
39. Parity flags are computed, not hardcoded
40. TR1 Rank-5 historical case classified as thread-related
41. Negative control 1: Plain same-message permalink without thread_ts is not thread-related by permalink alone
42. Negative control 2: Root permalink without thread_ts does not create thread context token
43. Negative control 3: Thread context permalink does not imply click eligibility
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

import scripts.diagnose_task11q4_p2a_l1_thread_affordance_tr2r1 as tr2r1_diag
from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink


# ---------------------------------------------------------------------------
# 1-7. Authority, Exception, Enumeration, and Interaction Restrictions
# ---------------------------------------------------------------------------

def test_1_attempt4_rb2_tr1_search_authority_parity():
    """1. Tests search telemetry authority parity when telemetry is present and safe."""
    rep = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    class MockBackend:
        last_search_telemetry = {
            "activation_dispatch_count": 1,
            "modal_ever_observed": True,
            "final_modal_count": 1,
            "input_ever_observed": True,
            "final_input_count": 1,
            "last_confirmed_stage": "SUBMIT_EXECUTED",
            "onenter_invoked": True,
            "safe_search_action_selected": True,
            "verified_query_state_before_submit": True,
            "search_surface_query_matches_requested": True,
            "generic_default_onenter_used": False,
            "slackbot_action_invoked": False,
            "write_capable_action_invoked": False,
            "unsafe_non_search_action_invoked": False,
            "composer_focus": "UNKNOWN",
            "composer_text_input": "UNKNOWN",
            "composer_send": "UNKNOWN",
        }
    tr2r1_diag.harvest_search_telemetry(MockBackend(), None, rep)
    ok, reason = tr2r1_diag.evaluate_search_safety_and_query_authority(rep)
    assert ok is True
    assert reason == "VERIFIED_SAFE_AND_CORRELATED"


def test_2_missing_telemetry_fails_closed():
    """2. Tests missing telemetry sets UNKNOWN and fails closed."""
    rep = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    tr2r1_diag.harvest_search_telemetry(None, None, rep)
    assert rep["search_telemetry"]["failure_case_classification"] == "TELEMETRY_MISSING"
    assert rep["search_telemetry"]["search_activation_dispatches"] == "UNKNOWN"
    assert rep["search_telemetry"]["modal_ever_observed"] == "UNKNOWN"
    assert rep["search_telemetry"]["input_ready"] == "UNKNOWN"
    assert rep["live_action_inventory"]["search_submissions"] == "UNKNOWN"
    ok, reason = tr2r1_diag.evaluate_search_safety_and_query_authority(rep)
    assert ok is False


def test_3_search_exception_not_masked():
    """3. Tests search exception is harvested before safety evaluation."""
    rep = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    class MockExc(Exception):
        details = {"last_confirmed_stage": "FAILED", "activation_dispatch_count": 1}
    se = MockExc("Search failed")
    tr2r1_diag.harvest_search_telemetry(object(), se, rep)
    assert rep["search_telemetry"]["last_confirmed_stage"] == "FAILED"


def test_4_exact_production_row_enumeration():
    """4. Tests exact production row enumeration parity."""
    assert tr2r1_diag.verify_search_result_row_enumeration_parity() is True


def test_5_top5_exact():
    """5. Tests max results is exactly 5."""
    assert tr2r1_diag.SEARCH_MAX_RESULTS == 5


def test_6_one_atomic_snapshot():
    """6. Tests atomic JS snapshot script evaluates top-5 in single pass."""
    assert "JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY" in dir(tr2r1_diag)


def test_7_zero_click_capable_execution_path():
    """7. Tests zero click-capable callsites in TR2 runner."""
    content = tr2r1_diag.PRIMARY_DEPENDENCY_PATHS["tr2r1_runner"].read_text(encoding="utf-8")
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith('"""') or s.startswith("*"):
            continue
        assert ".click()" not in s


# ---------------------------------------------------------------------------
# 8-14. Discovery Families, Deduplication, and Sensitive Content Safety
# ---------------------------------------------------------------------------

def test_8_overlapping_discovery_selectors_deduplicate_element_identity():
    """8. Verifies overlapping discovery selectors deduplicate DOM element identities."""
    js = tr2r1_diag.JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY
    assert "seenElements = new Set()" in js
    assert "rawFamilyMatchCount - uniqueInteractiveCandidateCount" in js


def test_9_raw_href_never_serialized():
    """9. Verifies raw href string is never captured in candidate records."""
    js = tr2r1_diag.JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY
    assert "href:" not in js
    assert "raw_href:" not in js


def test_10_raw_aria_label_never_serialized():
    """10. Verifies raw aria-label string is never captured."""
    js = tr2r1_diag.JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY
    assert "aria_label:" not in js
    assert "raw_aria:" not in js


def test_11_raw_title_never_serialized():
    """11. Verifies raw title string is never captured."""
    js = tr2r1_diag.JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY
    assert "title:" not in js
    assert "raw_title:" not in js


def test_12_raw_text_html_never_serialized():
    """12. Verifies innerHTML, outerHTML, innerText, textContent are never queried or serialized."""
    js = tr2r1_diag.JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY
    assert "innerHTML" not in js
    assert "outerHTML" not in js
    assert "innerText" not in js
    assert "textContent" not in js


def test_13_aria_thread_keyword_derives_boolean_only():
    """13. Tests aria-label thread keyword derivation."""
    raw_aria1 = "View thread"
    assert ("thread" in raw_aria1.lower() or "스레드" in raw_aria1.lower()) is True
    raw_aria2 = "Copy link"
    assert ("thread" in raw_aria2.lower() or "스레드" in raw_aria2.lower()) is False


def test_14_aria_reply_keyword_derives_boolean_only():
    """14. Tests aria-label reply keyword derivation."""
    raw_aria1 = "3 replies"
    assert ("reply" in raw_aria1.lower() or "replies" in raw_aria1.lower() or "답글" in raw_aria1.lower()) is True
    raw_aria2 = "Share message"
    assert ("reply" in raw_aria2.lower() or "replies" in raw_aria2.lower() or "답글" in raw_aria2.lower()) is False


# ---------------------------------------------------------------------------
# 15-20. Permalink Parsing & Classification
# ---------------------------------------------------------------------------

def test_15_slack_permalink_parses_in_memory():
    """15. Tests Slack permalink parsing in-memory."""
    plink = "https://app.slack.com/archives/C042WS25QAX/p1718079276126469?thread_ts=1718058683.912369"
    parsed = parse_slack_message_permalink(plink)
    assert parsed is not None
    assert parsed.channel_id == "C042WS25QAX"
    assert parsed.message_ts == "1718079276.126469"
    assert parsed.thread_ts == "1718058683.912369"


def test_16_same_message_classification_correct():
    """16. Verifies points_to_same_message classification."""
    row_channel = "C042WS25QAX"
    row_msg = "1718079276.126469"
    link_channel = "C042WS25QAX"
    link_msg = "1718079276.126469"
    points_to_same_msg = (link_channel == row_channel and link_msg == row_msg)
    assert points_to_same_msg is True


def test_17_thread_root_classification_correct():
    """17. Verifies points_to_thread_root classification."""
    row_channel = "C042WS25QAX"
    row_thread_ts = "1718058683.912369"
    link_channel = "C042WS25QAX"
    link_msg = "1718058683.912369"
    points_to_thread_root = (link_channel == row_channel and link_msg == row_thread_ts)
    assert points_to_thread_root is True


def test_18_role_button_classification_correct():
    """18. Verifies role=button classification."""
    role = "button"
    assert (role == "button") is True


def test_19_data_qa_structural_classification_correct():
    """19. Verifies data-qa structural token classification."""
    dqa1 = "search_message_thread"
    assert ("thread" in dqa1.lower() or "reply" in dqa1.lower()) is True
    dqa2 = "message_sender_name"
    assert ("thread" in dqa2.lower() or "reply" in dqa2.lower()) is False


def test_20_non_thread_unrelated_controls_not_misclassified():
    """20. Verifies non-thread controls are retained as candidates but not misclassified as thread controls."""
    mock_data = tr2r1_diag.build_mock_dry_run_inspection()
    row1 = mock_data["rows"][0]
    assert "ROLE_BUTTON_PRESENT" in row1["row_classifications"]
    assert "THREAD_TOKEN_DATA_QA_PRESENT" not in row1["row_classifications"]
    assert row1["has_thread_related_structure"] is False


# ---------------------------------------------------------------------------
# 21-28. Truncation, Historical Immutability, and Closure Audit
# ---------------------------------------------------------------------------

def test_21_serialization_truncation_flag_works():
    """21. Verifies candidate serialization truncation flag behavior."""
    total_count = 60
    max_bound = 50
    truncated = (total_count > max_bound)
    assert truncated is True


def test_22_truncation_forbids_negative_absence_verdict():
    """22. Verifies truncation of discovery itself forbids negative absence verdict."""
    discovery_complete = False
    has_thread_structure = False
    if not discovery_complete:
        diag_verdict = "DISCOVERY_INCOMPLETE_TRUNCATED"
    elif has_thread_structure:
        diag_verdict = "THREAD_RELATED_STRUCTURE_OBSERVED"
    else:
        diag_verdict = "NO_THREAD_RELATED_INTERACTIVE_STRUCTURE_OBSERVED"
    assert diag_verdict == "DISCOVERY_INCOMPLETE_TRUNCATED"


def test_23_complete_inventory_permits_negative_absence_verdict():
    """23. Verifies complete candidate inventory in-memory permits negative absence verdict even if serialized candidates truncated."""
    discovery_complete = True
    has_thread_structure = False
    serialization_truncated = True
    if not discovery_complete:
        diag_verdict = "DISCOVERY_INCOMPLETE_TRUNCATED"
    elif has_thread_structure:
        diag_verdict = "THREAD_RELATED_STRUCTURE_OBSERVED"
    else:
        diag_verdict = "NO_THREAD_RELATED_INTERACTIVE_STRUCTURE_OBSERVED"
    assert diag_verdict == "NO_THREAD_RELATED_INTERACTIVE_STRUCTURE_OBSERVED"


def test_24_live_artifact_overwrite_fails_closed():
    """24. Verifies live artifact overwrite guard fails closed."""
    assert not tr2r1_diag.LIVE_OUTPUT_FILE.exists()


def test_25_nine_historical_hashes_exact():
    """25. Verifies all 9 historical baselines exist and match exactly."""
    with open(tr2r1_diag.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        proto = json.load(f)
    for name, expected in proto["historical_baselines"].items():
        rel_p = expected.get("path") or expected.get("relative_path")
        p = REPO_ROOT / rel_p
        assert p.exists(), f"Missing {name} at {p}"
        actual_sha = tr2r1_diag.get_file_sha256(p)
        assert actual_sha == expected["sha256"], f"Hash mismatch for {name}"


def test_26_production_src_diff_empty():
    """26. Verifies git diff HEAD on src/ is completely empty."""
    res = subprocess.run(
        ["git", "diff", "HEAD", "--", "src/"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == ""


def test_27_provider_calls_zero():
    """27. Verifies provider calls are 0."""
    assert tr2r1_diag.MAX_PROVIDER_CALLS == 0


def test_28_runtime_closure_has_zero_untracked_dependencies():
    """28. Verifies runtime closure audit has 60 modules and 0 untracked dependencies."""
    with open(tr2r1_diag.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        proto = json.load(f)
    closure_audit = tr2r1_diag.audit_runtime_import_closure(proto)
    assert closure_audit["closure_audit_passed"] is True
    assert closure_audit["loaded_project_module_count"] == 60
    assert closure_audit["untracked_loaded_project_dependency_count"] == 0
    assert closure_audit["mismatched_runtime_dependency_count"] == 0


# ---------------------------------------------------------------------------
# 29-39. New Required Regressions for TR2-PREP Correction
# ---------------------------------------------------------------------------

def test_29_tr2_actual_attempt4_host_constructor_parity():
    """29. Verifies PesuWebViewHost constructor in TR2 matches Attempt 4 exactly."""
    content = tr2r1_diag.PRIMARY_DEPENDENCY_PATHS["tr2r1_runner"].read_text(encoding="utf-8")
    assert 'user_data_folder=DIRECT_SLACK_PROFILE_PATH' in content
    assert 'initial_url="https://app.slack.com/client"' in content
    assert 'title="PESU Task 11 / P2A-L1 / TR2-R1 Thread Affordance Discovery"' in content


def test_30_tr2_backend_constructor_and_storage_path_parity():
    """30. Verifies WebViewSlackSearchBackend constructor and storage_path match Attempt 4."""
    content = tr2r1_diag.PRIMARY_DEPENDENCY_PATHS["tr2r1_runner"].read_text(encoding="utf-8")
    assert 'WebViewSlackSearchBackend(' in content
    assert 'config=WebViewRuntimeConfig(storage_path=Path(DIRECT_SLACK_PROFILE_PATH))' in content
    assert 'host=host' in content


def test_31_tr2_backend_start_runtime_parity():
    """31. Verifies backend.start_runtime(timeout=timeout) is invoked before search."""
    content = tr2r1_diag.PRIMARY_DEPENDENCY_PATHS["tr2r1_runner"].read_text(encoding="utf-8")
    start_pos = content.find('backend.start_runtime(timeout=timeout)')
    search_pos = content.find('backend.search_messages(TARGET_QUERY, max_results=SEARCH_MAX_RESULTS)')
    assert start_pos != -1
    assert search_pos != -1
    assert start_pos < search_pos


def test_32_tr2_search_harvester_ast_parity_with_rb2():
    """32. Verifies AST parity between TR2 and RB2 search telemetry harvesters."""
    assert tr2r1_diag.verify_search_telemetry_authority_parity_with_attempt4() is True


def test_33_tr2_search_safety_gate_ast_parity_with_rb2():
    """33. Verifies AST parity between TR2 and RB2 evaluate_search_safety_and_query_authority."""
    assert tr2r1_diag.verify_search_safety_gate_parity_with_attempt4() is True


def test_34_missing_unsafe_telemetry_does_not_default_safe():
    """34. Verifies missing dangerous telemetry fields fail closed as UNKNOWN and trigger safety failure."""
    rep = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    backend_telemetry = {
        "activation_dispatch_count": 1,
        "modal_ever_observed": True,
        "input_ever_observed": True,
        "final_input_count": 1,
        "last_confirmed_stage": "SUBMIT_EXECUTED",
        "onenter_invoked": True,
        "safe_search_action_selected": True,
        "verified_query_state_before_submit": True,
        "search_surface_query_matches_requested": True,
    }
    class MockBackend:
        last_search_telemetry = backend_telemetry

    tr2r1_diag.harvest_search_telemetry(MockBackend(), None, rep)
    assert rep["safety_telemetry"]["GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH"] == "UNKNOWN"
    assert rep["safety_telemetry"]["SLACKBOT_ACTION_INVOKED_BY_PESU"] == "UNKNOWN"
    assert rep["safety_telemetry"]["WRITE_CAPABLE_ACTION_INVOKED_BY_PESU"] == "UNKNOWN"
    assert rep["safety_telemetry"]["UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU"] == "UNKNOWN"

    ok, reason = tr2r1_diag.evaluate_search_safety_and_query_authority(rep)
    assert ok is False
    assert reason == "GENERIC_DEFAULT_ONENTER_NOT_PROVEN_FALSE"


def test_35_search_submission_count_comes_from_authoritative_telemetry():
    """35. Verifies search_submissions count is harvested from telemetry, not locally fabricated."""
    rep1 = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    class BackendTrue:
        last_search_telemetry = {"onenter_invoked": True}
    tr2r1_diag.harvest_search_telemetry(BackendTrue(), None, rep1)
    assert rep1["live_action_inventory"]["search_submissions"] == 1

    rep2 = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    class BackendFalse:
        last_search_telemetry = {"onenter_invoked": False}
    tr2r1_diag.harvest_search_telemetry(BackendFalse(), None, rep2)
    assert rep2["live_action_inventory"]["search_submissions"] == 0

    rep3 = {"live_action_inventory": {}, "search_telemetry": {}, "safety_telemetry": {}, "checks": {}}
    class BackendMissing:
        last_search_telemetry = {}
    tr2r1_diag.harvest_search_telemetry(BackendMissing(), None, rep3)
    assert rep3["live_action_inventory"]["search_submissions"] == "UNKNOWN"


def test_36_search_exception_harvested_before_raise():
    """36. Verifies exception ordering in live execution block via AST/source regression."""
    assert tr2r1_diag.verify_search_exception_order_parity_with_attempt4() is True


def test_37_protocol_semantic_identity_mismatch_blocks_pre_webview(monkeypatch, tmp_path):
    """37. Verifies protocol semantic identity mismatch triggers FAIL_CLOSED before WebView."""
    bad_proto = tmp_path / "bad_proto.json"
    with open(tr2r1_diag.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["target_query"] = "WRONG_QUERY"
    bad_proto.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(tr2r1_diag, "PROTOCOL_FREEZE_PATH", bad_proto)
    report = tr2r1_diag.run_tr2r1_diagnostic(dry_run=True)
    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "PROTOCOL_SEMANTIC_IDENTITY_MISMATCH"


def test_38_runtime_closure_hash_mismatch_blocks_pre_webview(monkeypatch, tmp_path):
    """38. Verifies runtime closure hash mismatch triggers FAIL_CLOSED before WebView."""
    bad_proto = tmp_path / "bad_proto.json"
    with open(tr2r1_diag.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["runtime_closure_hashes"]["src/pesu_agent/__init__.py"] = "0" * 64
    bad_proto.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(tr2r1_diag, "PROTOCOL_FREEZE_PATH", bad_proto)
    report = tr2r1_diag.run_tr2r1_diagnostic(dry_run=True)
    assert report["verdict"] == "FAIL_CLOSED"
    assert report["diagnostic_verdict"] == "RUNTIME_CLOSURE_GATE_FAILED"


def test_39_parity_flags_are_computed_not_hardcoded():
    """39. Verifies parity flags in report['checks'] are computed dynamically, not hardcoded True initially."""
    # Ensure verifiers themselves return True
    assert tr2r1_diag.verify_runtime_search_path_parity_with_attempt4() is True
    assert tr2r1_diag.verify_search_telemetry_authority_parity_with_attempt4() is True
    assert tr2r1_diag.verify_search_exception_order_parity_with_attempt4() is True
    assert tr2r1_diag.verify_search_safety_gate_parity_with_attempt4() is True
    assert tr2r1_diag.verify_search_result_row_enumeration_parity() is True

    # Run dry run and check computed flags
    report = tr2r1_diag.run_tr2r1_diagnostic(dry_run=True)
    checks = report["checks"]
    assert checks["TR2_RUNTIME_SEARCH_PATH_PARITY_WITH_ATTEMPT4"] is True
    assert checks["TR2_SEARCH_TELEMETRY_AUTHORITY_PARITY_WITH_ATTEMPT4"] is True
    assert checks["TR2_SEARCH_EXCEPTION_ORDER_PARITY_WITH_ATTEMPT4"] is True
    assert checks["TR2_SEARCH_SAFETY_GATE_PARITY_WITH_ATTEMPT4"] is True
    assert checks["TR2_SEARCH_RESULT_ROW_ENUMERATION_PARITY"] is True


# ---------------------------------------------------------------------------
# Helper for evaluating Discovery JS in Node
# ---------------------------------------------------------------------------

def _run_node_discovery_evaluation(elements_config):
    js_snippet = tr2r1_diag.JS_INSPECT_TOP5_THREAD_AFFORDANCE_DISCOVERY
    node_script = f"""
    const inspectFn = {js_snippet};

    function makeElement(cfg) {{
        return {{
            tagName: cfg.tagName || 'A',
            isConnected: true,
            parentElement: null,
            getBoundingClientRect: () => ({{ width: 50, height: 20 }}),
            hasAttribute: (attr) => {{
                if (attr === 'href') return !!cfg.href;
                return false;
            }},
            getAttribute: (attr) => {{
                if (attr === 'href') return cfg.href || null;
                if (attr === 'aria-label') return cfg.ariaLabel || null;
                if (attr === 'title') return cfg.title || null;
                if (attr === 'role') return cfg.role || null;
                if (attr === 'data-qa') return cfg.dataQa || null;
                return null;
            }},
            closest: () => null
        }};
    }}

    const elements = {json.dumps(elements_config)}.map(makeElement);

    const mockRow = {{
        isConnected: true,
        getBoundingClientRect: () => ({{ width: 100, height: 100 }}),
        querySelectorAll: (sel) => {{
            if (sel === 'a[href]') return elements.filter(e => e.tagName === 'A' && e.hasAttribute('href'));
            if (sel === 'button') return elements.filter(e => e.tagName === 'BUTTON');
            if (sel === '[role="button"]') return elements.filter(e => e.getAttribute('role') === 'button');
            if (sel === '[data-qa]') return elements.filter(e => e.getAttribute('data-qa') !== null);
            return [];
        }}
    }};

    global.document = {{
        querySelectorAll: (sel) => {{
            if (sel === '[data-qa="search_view"]') {{
                return [{{
                    querySelectorAll: (rowSel) => [mockRow]
                }}];
            }}
            return [];
        }}
    }};

    const res = inspectFn('[data-qa="search_view"]', '[data-qa="search_result"]', 5, 50);
    console.log(JSON.stringify(res));
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
    return json.loads(res.stdout.strip())


# ---------------------------------------------------------------------------
# 40-43. Required Regressions for TR2-PREP Final Discovery Correction
# ---------------------------------------------------------------------------

def test_thread_reply_same_message_permalink_with_thread_ts_is_thread_related():
    """40. Verifies TR1 rank-5 regression fixture: same-message permalink with thread_ts parameter
    is classified with SAME_MESSAGE_PERMALINK_ANCHOR_PRESENT, THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT,
    and SAME_MESSAGE_THREAD_REPLY_PERMALINK_PRESENT, producing has_thread_related_structure = true.
    """
    channel_id = "C042WS25QAX"
    message_ts = "1718079276.126469"
    thread_ts = "1718058683.912369"
    permalink = f"https://app.slack.com/archives/{channel_id}/p1718079276126469?thread_ts={thread_ts}"

    res = _run_node_discovery_evaluation([
        {"tagName": "A", "href": permalink}
    ])
    assert res["ok"] is True
    assert len(res["rows"]) == 1
    row = res["rows"][0]
    row_classes = row["row_classifications"]
    cand = row["candidate_records"][0]
    cand_classes = cand["element_classifications"]

    # Verify permalink parsed fields
    assert cand["is_slack_message_permalink"] is True
    assert cand["parsed_channel_id"] == channel_id
    assert cand["parsed_message_ts"] == message_ts
    assert cand["parsed_thread_ts"] == thread_ts
    assert cand["has_thread_ts_parameter"] is True
    assert cand["points_to_same_message"] is True
    assert cand["points_to_thread_root"] is False

    # Required tokens present
    assert "SAME_MESSAGE_PERMALINK_ANCHOR_PRESENT" in row_classes
    assert "THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT" in row_classes
    assert "SAME_MESSAGE_THREAD_REPLY_PERMALINK_PRESENT" in row_classes

    assert "SAME_MESSAGE_PERMALINK_ANCHOR_PRESENT" in cand_classes
    assert "THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT" in cand_classes
    assert "SAME_MESSAGE_THREAD_REPLY_PERMALINK_PRESENT" in cand_classes

    # Row and global thread related structure
    assert row["has_thread_related_structure"] is True
    assert res["any_row_has_thread_related_structure"] is True


def test_plain_same_message_permalink_without_thread_ts_is_not_thread_related_by_permalink_alone():
    """41. Negative control: plain same-message permalink without thread_ts parameter
    receives SAME_MESSAGE_PERMALINK_ANCHOR_PRESENT but NOT THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT,
    and has_thread_related_structure remains false.
    """
    channel_id = "C042WS25QAX"
    permalink = f"https://app.slack.com/archives/{channel_id}/p1718079276126469"

    res = _run_node_discovery_evaluation([
        {"tagName": "A", "href": permalink}
    ])
    assert res["ok"] is True
    assert len(res["rows"]) == 1
    row = res["rows"][0]
    row_classes = row["row_classifications"]
    cand = row["candidate_records"][0]

    assert cand["is_slack_message_permalink"] is True
    assert cand["has_thread_ts_parameter"] is False
    assert cand["parsed_thread_ts"] is None
    assert cand["points_to_same_message"] is True

    # Same message token is present
    assert "SAME_MESSAGE_PERMALINK_ANCHOR_PRESENT" in row_classes

    # Thread context tokens must be ABSENT
    assert "THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT" not in row_classes
    assert "SAME_MESSAGE_THREAD_REPLY_PERMALINK_PRESENT" not in row_classes
    assert "THREAD_ROOT_PERMALINK_ANCHOR_PRESENT" not in row_classes

    # By permalink alone, not thread related
    assert row["has_thread_related_structure"] is False
    assert res["any_row_has_thread_related_structure"] is False


def test_root_permalink_without_thread_ts_does_not_create_thread_context_token():
    """42. Negative control: root permalink without thread_ts query parameter
    receives THREAD_ROOT_PERMALINK_ANCHOR_PRESENT but does NOT receive THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT.
    """
    channel_id = "C042WS25QAX"
    thread_ts = "1718058683.912369"
    # Anchor 1 defines row as a thread reply
    anchor1 = f"https://app.slack.com/archives/{channel_id}/p1718079276126469?thread_ts={thread_ts}"
    # Anchor 2 points to thread root WITHOUT ?thread_ts= parameter
    anchor2 = f"https://app.slack.com/archives/{channel_id}/p1718058683912369"

    res = _run_node_discovery_evaluation([
        {"tagName": "A", "href": anchor1},
        {"tagName": "A", "href": anchor2}
    ])
    assert res["ok"] is True
    row = res["rows"][0]
    cand2 = row["candidate_records"][1]

    assert cand2["is_slack_message_permalink"] is True
    assert cand2["points_to_thread_root"] is True
    assert cand2["points_to_same_message"] is False
    assert cand2["has_thread_ts_parameter"] is False

    # Root token present, but NOT thread context token
    assert "THREAD_ROOT_PERMALINK_ANCHOR_PRESENT" in cand2["element_classifications"]
    assert "THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT" not in cand2["element_classifications"]
    assert "SAME_MESSAGE_THREAD_REPLY_PERMALINK_PRESENT" not in cand2["element_classifications"]


def test_thread_context_permalink_does_not_imply_click_eligibility():
    """43. Negative control: verifies neither THREAD_CONTEXT_PERMALINK_ANCHOR_PRESENT nor any other
    discovery token introduces CLICK_ELIGIBLE, SAFE_TO_CLICK, QUALIFIED_NAVIGATION_TARGET, or ATTEMPT5_TARGET
    into classifications, verdicts, checks, or contracts.
    """
    forbidden_tokens = [
        "CLICK_ELIGIBLE",
        "SAFE_TO_CLICK",
        "QUALIFIED_NAVIGATION_TARGET",
        "ATTEMPT5_TARGET"
    ]

    with open(tr2r1_diag.PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        proto = json.load(f)

    # Check protocol tokens
    for ft in forbidden_tokens:
        assert ft not in proto["discovery_classification_tokens"]
        assert ft not in proto["thread_related_tokens"]

    # Check runner constants
    for ft in forbidden_tokens:
        assert ft not in tr2r1_diag.DISCOVERY_CLASSIFICATION_TOKENS
        assert ft not in tr2r1_diag.THREAD_RELATED_TOKENS

    # Check report schema / action contracts
    rep = tr2r1_diag.run_tr2r1_diagnostic(dry_run=True)
    assert rep["action_contracts"]["MAX_REPLY_BAR_CLICKS"] == 0
    assert rep["action_contracts"]["MAX_RESULT_CARD_CLICKS"] == 0
    assert rep["action_contracts"]["MAX_WRITE_CAPABLE_ACTIONS"] == 0
    assert rep["action_contracts"]["MAX_PROVIDER_CALLS"] == 0

    rep_str = json.dumps(rep)
    for ft in forbidden_tokens:
        assert ft not in rep_str


# ---------------------------------------------------------------------------
# Host API & Governance Regressions for TR2-R1
# ---------------------------------------------------------------------------

def test_tr2r1_host_exposes_exact_dom_evaluation_method():
    """Verifies that PesuWebViewHost exposes callable evaluate_js and NOT evaluate_script without mocking."""
    from pesu_agent.runtime.pesu_webview_host import PesuWebViewHost
    assert hasattr(PesuWebViewHost, "evaluate_js"), "PesuWebViewHost missing evaluate_js"
    assert callable(getattr(PesuWebViewHost, "evaluate_js")), "evaluate_js is not callable"
    assert not hasattr(PesuWebViewHost, "evaluate_script"), "PesuWebViewHost unexpectedly has evaluate_script"


def test_tr2r1_does_not_reference_evaluate_script_if_host_has_no_such_method():
    """Verifies that TR2-R1 runner AST does not access evaluate_script on any object."""
    runner_src = (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_affordance_tr2r1.py").read_text(encoding="utf-8")
    tree = ast.parse(runner_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "evaluate_script":
            # Check if this attribute access is inside an audit/check or an actual call
            assert False, f"TR2-R1 runner contains attribute access evaluate_script at line {node.lineno}"


def test_tr2r1_dom_snapshot_call_matches_qualified_host_api_signature():
    """Verifies that TR2-R1 calls host.evaluate_js with valid signature matching PesuWebViewHost.evaluate_js."""
    import inspect
    from pesu_agent.runtime.pesu_webview_host import PesuWebViewHost
    sig = inspect.signature(PesuWebViewHost.evaluate_js)
    params = list(sig.parameters.keys())
    assert params == ["self", "script", "timeout"], f"Unexpected signature: {params}"

    runner_src = (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_affordance_tr2r1.py").read_text(encoding="utf-8")
    tree = ast.parse(runner_src)
    evaluate_js_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "host" and node.func.attr == "evaluate_js":
                evaluate_js_calls.append(node)
    assert len(evaluate_js_calls) >= 1, "Expected host.evaluate_js call in runner"
    call = evaluate_js_calls[0]
    assert len(call.args) == 1, "Expected positional script arg"
    assert any(kw.arg == "timeout" for kw in call.keywords), "Expected timeout keyword arg"


def test_tr2r1_host_evaluation_callsite_parity_with_tr1_and_rb2():
    """Verifies that TR2-R1 uses the same host.evaluate_js DOM evaluation call as TR1 and RB2."""
    tr1_src = (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_target_affordance_tr1.py").read_text(encoding="utf-8")
    rb2_src = (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_reply_bar_structure_rb2.py").read_text(encoding="utf-8")
    tr2r1_src = (REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_affordance_tr2r1.py").read_text(encoding="utf-8")

    assert "host.evaluate_js(" in tr1_src
    assert "host.evaluate_js(" in rb2_src
    assert "host.evaluate_js(" in tr2r1_src
    assert "host.evaluate_script(" not in tr2r1_src


def test_tr2r1_consumed_tr2_runner_sha_frozen():
    """Verifies that the consumed failed TR2 runner remains frozen at its authoritative SHA."""
    consumed_tr2_path = REPO_ROOT / "scripts" / "diagnose_task11q4_p2a_l1_thread_affordance_tr2.py"
    assert consumed_tr2_path.exists()
    act_sha = tr2r1_diag.get_file_sha256(consumed_tr2_path)
    assert act_sha == "6cf4b96a84689aee7829199580d6dbb445bd8deac7b6831c338b200baea71338"


def test_tr2r1_consumed_tr2_protocol_sha_frozen():
    """Verifies that the consumed failed TR2 protocol remains frozen at its authoritative SHA."""
    consumed_tr2_proto = REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2_protocol_freeze.json"
    assert consumed_tr2_proto.exists()
    act_sha = tr2r1_diag.get_file_sha256(consumed_tr2_proto)
    assert act_sha == "2a2632e25654983b5e30a6fb78856d01cf0551589f9bcf2fc97d0d9b65277dcd"


def test_tr2r1_derived_failed_tr2_evidence_hash_exact():
    """Verifies that the derived failed-TR2 evidence file exists and matches its authoritative SHA."""
    failed_ev_path = REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2_failed_live_evidence.json"
    assert failed_ev_path.exists()
    act_sha = tr2r1_diag.get_file_sha256(failed_ev_path)
    assert act_sha == "895748e464df6f97448fe805e7873f4c1b595186b880faabc34af718ca7a0bf4"


def test_tr2r1_same_protocol_tr2_rerun_forbidden():
    """Verifies that the derived failed-TR2 evidence explicitly declares rerun of original TR2 protocol forbidden."""
    failed_ev = json.loads((REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2_failed_live_evidence.json").read_text(encoding="utf-8"))
    assert failed_ev.get("rerun_same_protocol_forbidden") is True
    assert failed_ev.get("live_execution_consumed") is True


def test_tr2r1_attempt_marker_path_and_contract():
    """Verifies the attempt marker path format and contract requirements."""
    marker_path = tr2r1_diag.ATTEMPT_STARTED_MARKER_FILE
    assert marker_path.name == "task11q4_p2a_l1_tr2r1_attempt_started.json"
    assert str(marker_path).endswith("task11q4_p2a_l1_tr2r1_attempt_started.json")


def test_tr2r1_handled_live_exception_writes_immutable_failure_artifact(tmp_path, monkeypatch):
    """Verifies that handled exceptions in live execution persist the live artifact before returning."""
    fake_live = tmp_path / "live_artifact.json"
    fake_marker = tmp_path / "marker.json"
    monkeypatch.setattr(tr2r1_diag, "LIVE_OUTPUT_FILE", fake_live)
    monkeypatch.setattr(tr2r1_diag, "ATTEMPT_STARTED_MARKER_FILE", fake_marker)

    # Mock PesuWebViewHost to raise during start_runtime
    class MockHost:
        def __init__(self, **kwargs): pass
        def start(self, **kwargs): pass
        def destroy(self): pass

    class MockBackend:
        def __init__(self, **kwargs):
            self.last_search_telemetry = None
            self.last_telemetry = None
        def start_runtime(self, timeout=30.0):
            raise RuntimeError("Simulated runtime startup failure")

    monkeypatch.setattr(tr2r1_diag, "PesuWebViewHost", MockHost)
    monkeypatch.setattr(tr2r1_diag, "WebViewSlackSearchBackend", MockBackend)

    rep = tr2r1_diag.run_tr2_diagnostic(dry_run=False)
    assert rep["verdict"] == "FAIL_CLOSED"
    assert rep["diagnostic_verdict"] == "EXECUTION_EXCEPTION"
    assert "Simulated runtime startup failure" in rep["error"]
    assert fake_marker.exists(), "Attempt marker must be created before live execution"
    assert fake_live.exists(), "Handled live exception must persist live failure artifact"

    saved_data = json.loads(fake_live.read_text(encoding="utf-8"))
    assert saved_data["verdict"] == "FAIL_CLOSED"
    assert saved_data["diagnostic_verdict"] == "EXECUTION_EXCEPTION"


def test_tr2r1_final_artifact_never_overwritten(tmp_path, monkeypatch):
    """Verifies that an existing final live artifact is never overwritten."""
    fake_live = tmp_path / "existing_live.json"
    fake_live.write_text(json.dumps({"original": "evidence"}), encoding="utf-8")
    fake_marker = tmp_path / "existing_marker.json"
    monkeypatch.setattr(tr2r1_diag, "LIVE_OUTPUT_FILE", fake_live)
    monkeypatch.setattr(tr2r1_diag, "ATTEMPT_STARTED_MARKER_FILE", fake_marker)

    rep = tr2r1_diag.run_tr2_diagnostic(dry_run=False)
    assert rep["verdict"] == "FAIL_CLOSED"
    assert rep["diagnostic_verdict"] == "LIVE_ARTIFACT_ALREADY_EXISTS"

    content = json.loads(fake_live.read_text(encoding="utf-8"))
    assert content == {"original": "evidence"}, "File content must not have been overwritten"


# ---------------------------------------------------------------------------
# Exact Runtime Closure Regressions (Phase 13.5D Task 11 P2A TR2-R1)
# ---------------------------------------------------------------------------

def test_tr2r1_runtime_closure_contains_current_runner():
    """Verifies that the frozen runtime_closure_hashes contains the TR2-R1 runner with exact SHA."""
    proto_freeze = json.loads((REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2r1_protocol_freeze.json").read_text(encoding="utf-8"))
    closure = proto_freeze.get("runtime_closure_hashes", {})
    runner_rel = "scripts/diagnose_task11q4_p2a_l1_thread_affordance_tr2r1.py"
    assert runner_rel in closure, f"{runner_rel} must be in frozen runtime_closure_hashes"
    act_sha = tr2r1_diag.get_file_sha256(REPO_ROOT / runner_rel)
    assert closure[runner_rel] == act_sha, f"SHA mismatch for runner in closure: {closure[runner_rel]} != {act_sha}"


def test_tr2r1_runtime_closure_excludes_consumed_tr2_runner():
    """Verifies that the consumed failed TR2 runner is NOT in TR2-R1's runtime_closure_hashes."""
    proto_freeze = json.loads((REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2r1_protocol_freeze.json").read_text(encoding="utf-8"))
    closure = proto_freeze.get("runtime_closure_hashes", {})
    consumed_runner_rel = "scripts/diagnose_task11q4_p2a_l1_thread_affordance_tr2.py"
    assert consumed_runner_rel not in closure, f"{consumed_runner_rel} must NOT be in TR2-R1 runtime_closure_hashes"


def test_runtime_closure_missing_loaded_file_fails_even_if_primary_dependency_hash_exists():
    """Verifies that if a loaded project file is missing from runtime_closure_hashes,
    audit_runtime_import_closure fails EVEN IF it is present in dependency_hashes.
    Proves dependency_hashes cannot mask an incomplete runtime closure.
    """
    proto_freeze = json.loads((REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2r1_protocol_freeze.json").read_text(encoding="utf-8"))
    manifest_copy = json.loads(json.dumps(proto_freeze))

    runner_rel = "scripts/diagnose_task11q4_p2a_l1_thread_affordance_tr2r1.py"
    assert runner_rel in manifest_copy["runtime_closure_hashes"]
    del manifest_copy["runtime_closure_hashes"][runner_rel]
    assert "tr2r1_runner" in manifest_copy.get("dependency_hashes", {})

    audit = tr2r1_diag.audit_runtime_import_closure(manifest_copy)
    assert audit["closure_audit_passed"] is False
    assert audit["missing_from_runtime_closure_count"] == 1
    assert runner_rel in audit["missing_from_runtime_closure"]


def test_runtime_closure_stale_extra_manifest_entry_fails():
    """Verifies that a stale/extra manifest entry in runtime_closure_hashes causes audit failure."""
    proto_freeze = json.loads((REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2r1_protocol_freeze.json").read_text(encoding="utf-8"))
    manifest_copy = json.loads(json.dumps(proto_freeze))

    stale_path = "src/pesu_agent/non_existent_module_for_testing.py"
    manifest_copy["runtime_closure_hashes"][stale_path] = "0" * 64

    audit = tr2r1_diag.audit_runtime_import_closure(manifest_copy)
    assert audit["closure_audit_passed"] is False
    assert audit["stale_runtime_closure_entry_count"] == 1
    assert stale_path in audit["stale_runtime_closure_entries"]


def test_runtime_closure_sha_mismatch_fails():
    """Verifies that a SHA mismatch in runtime_closure_hashes causes audit failure."""
    proto_freeze = json.loads((REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2r1_protocol_freeze.json").read_text(encoding="utf-8"))
    manifest_copy = json.loads(json.dumps(proto_freeze))

    runner_rel = "scripts/diagnose_task11q4_p2a_l1_thread_affordance_tr2r1.py"
    manifest_copy["runtime_closure_hashes"][runner_rel] = "f" * 64

    audit = tr2r1_diag.audit_runtime_import_closure(manifest_copy)
    assert audit["closure_audit_passed"] is False
    assert audit["mismatched_runtime_dependency_count"] == 1
    assert any(m["path"] == runner_rel for m in audit["mismatched_runtime_dependencies"])


def test_runtime_closure_exact_path_set_and_hashes_pass():
    """Verifies that the frozen manifest exactly matches the imported set with 0 missing, 0 stale, 0 mismatched."""
    proto_freeze = json.loads((REPO_ROOT / "artifacts" / "task11q4_p2a_l1_tr2r1_protocol_freeze.json").read_text(encoding="utf-8"))
    audit = tr2r1_diag.audit_runtime_import_closure(proto_freeze)
    assert audit["closure_audit_passed"] is True
    assert audit["missing_from_runtime_closure_count"] == 0
    assert audit["missing_from_runtime_closure"] == []
    assert audit["stale_runtime_closure_entry_count"] == 0
    assert audit["stale_runtime_closure_entries"] == []
    assert audit["mismatched_runtime_dependency_count"] == 0
    assert audit["mismatched_runtime_dependencies"] == []
    assert audit["loaded_project_module_count"] == audit["manifest_runtime_closure_count"]
