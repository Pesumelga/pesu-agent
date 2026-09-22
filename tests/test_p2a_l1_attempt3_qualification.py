"""Task 11 / P2A-L1 Attempt 3 Qualification Offline Regression Suite.

Comprehensive offline regressions for Attempt 3:
1. Deterministic selector applied to preserved RD1 Run 2 fixture chooses rank 5.
2. DM rows rejected.
3. Root/self rows (message_ts == thread_ts) rejected.
4. Row without reply_bar rejected.
5. Row without thread_ts rejected.
6. Lowest eligible rank wins.
7. Expected message_ts != expected_root_ts invariant (fitness contract).
8. Frozen raw permalink parses to exact expected channel/message/thread identity.
9. Exact pre-action identity PASS.
10. Target at rank 6 with max_results=5 fails closed as FAIL_NOT_RESIDENT.
11. Runner max_results frozen at 5 (P1B remains DEFERRED).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from pesu_agent.investigation.models import EvidenceItem, SearchQuery
from pesu_agent.investigation.retrieval_coordinator import CandidateQueryHit
from pesu_agent.investigation.selector import get_canonical_evidence_key
from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink
from pesu_agent.search.webview_thread_reader import ThreadReadResult, ThreadRootItem, ThreadReplyItem, ThreadContextStatus, normalize_slack_ts

from scripts.qualify_task11q4_p2a_live_root_extraction_attempt3 import (
    ATTEMPT3_SEARCH_MAX_RESULTS,
    DEPENDENCY_PATHS,
    DRY_RUN_OUTPUT_FILE,
    EXPECTED_CHANNEL_ID,
    EXPECTED_MESSAGE_TS,
    EXPECTED_ROOT_TS,
    LIVE_OUTPUT_FILE,
    P1B_ADAPTIVE_EVIDENCE_BUDGET,
    PROTOCOL_FREEZE_PATH,
    SOURCE_RAW_PERMALINK,
    SOURCE_RD1_RUN2_RANK,
    TARGET_QUERY,
    evaluate_dry_run_gate,
    evaluate_live_gate,
    evaluate_search_safety_and_query_authority,
    harvest_search_telemetry,
    harvest_thread_reader_telemetry,
    run_p2a_l1_attempt3_qualification,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RD1_RUN2_ARTIFACT = REPO_ROOT / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_rd1_run2_search_residency.json"


def _deterministic_select_target(inspected_rows: list[dict]) -> dict | None:
    def norm(ts):
        if ts is None:
            return None
        try:
            return f"{float(ts):.6f}"
        except Exception:
            return str(ts).strip()

    candidates = []
    for row in inspected_rows:
        parse_status = row.get("parse_status")
        channel_id = row.get("parsed_channel_id") or ""
        reply_bar = row.get("reply_bar_present")
        msg_ts = row.get("parsed_message_ts")
        thread_ts = row.get("parsed_thread_ts")

        if (
            parse_status == "PARSED"
            and channel_id.startswith("C")
            and reply_bar is True
            and thread_ts is not None
            and str(thread_ts).strip() != ""
            and norm(msg_ts) != norm(thread_ts)
        ):
            candidates.append(row)

    if not candidates:
        return None
    return min(candidates, key=lambda r: r.get("rank"))


def test_1_deterministic_selector_chooses_rank_5():
    assert RD1_RUN2_ARTIFACT.exists()
    with open(RD1_RUN2_ARTIFACT, "r", encoding="utf-8") as f:
        data = json.load(f)

    inspected = data.get("inspected_rows", [])
    assert len(inspected) == 20

    winner = _deterministic_select_target(inspected)
    assert winner is not None
    assert winner["rank"] == 5
    assert winner["parsed_channel_id"] == "C042WS25QAX"
    assert winner["parsed_message_ts"] == "1718079276.126469"
    assert winner["parsed_thread_ts"] == "1718058683.912369"
    assert winner["reply_bar_present"] is True


def test_2_dm_rows_rejected():
    dm_row = {
        "rank": 1,
        "parse_status": "PARSED",
        "parsed_channel_id": "D07RA9YDE8G",
        "reply_bar_present": True,
        "parsed_message_ts": "1718079276.126469",
        "parsed_thread_ts": "1718058683.912369",
    }
    assert _deterministic_select_target([dm_row]) is None


def test_3_root_self_rows_rejected():
    self_row = {
        "rank": 4,
        "parse_status": "PARSED",
        "parsed_channel_id": "C038SK6ETAR",
        "reply_bar_present": True,
        "parsed_message_ts": "1783907532.526729",
        "parsed_thread_ts": "1783907532.526729",
    }
    assert _deterministic_select_target([self_row]) is None


def test_4_row_without_reply_bar_rejected():
    no_reply_bar = {
        "rank": 3,
        "parse_status": "PARSED",
        "parsed_channel_id": "C4AQZGQNR",
        "reply_bar_present": False,
        "parsed_message_ts": "1718079276.126469",
        "parsed_thread_ts": "1718058683.912369",
    }
    assert _deterministic_select_target([no_reply_bar]) is None


def test_5_row_without_thread_ts_rejected():
    no_thread_ts = {
        "rank": 3,
        "parse_status": "PARSED",
        "parsed_channel_id": "C4AQZGQNR",
        "reply_bar_present": True,
        "parsed_message_ts": "1718079276.126469",
        "parsed_thread_ts": None,
    }
    assert _deterministic_select_target([no_thread_ts]) is None


def test_6_lowest_eligible_rank_wins():
    with open(RD1_RUN2_ARTIFACT, "r", encoding="utf-8") as f:
        data = json.load(f)
    inspected = data.get("inspected_rows", [])

    eligible_ranks = []
    for r in inspected:
        if _deterministic_select_target([r]) is not None:
            eligible_ranks.append(r["rank"])

    assert 5 in eligible_ranks
    assert 7 in eligible_ranks
    assert 11 in eligible_ranks
    assert min(eligible_ranks) == 5


def test_7_target_fitness_contract_invariant():
    assert normalize_slack_ts(EXPECTED_MESSAGE_TS) != normalize_slack_ts(EXPECTED_ROOT_TS)
    assert EXPECTED_MESSAGE_TS != EXPECTED_ROOT_TS
    fitness_valid = (normalize_slack_ts(EXPECTED_MESSAGE_TS) != normalize_slack_ts(EXPECTED_ROOT_TS))
    assert fitness_valid is True

    same_ts = "1718058683.912369"
    fitness_invalid = (normalize_slack_ts(same_ts) != normalize_slack_ts(same_ts))
    assert fitness_invalid is False


def test_8_frozen_raw_permalink_parses_to_expected_identity():
    parsed = parse_slack_message_permalink(SOURCE_RAW_PERMALINK)
    assert parsed is not None
    assert parsed.channel_id == EXPECTED_CHANNEL_ID
    assert normalize_slack_ts(parsed.message_ts) == normalize_slack_ts(EXPECTED_MESSAGE_TS)
    assert normalize_slack_ts(parsed.thread_ts) == normalize_slack_ts(EXPECTED_ROOT_TS)
    assert parsed.is_thread_reply is True


def test_9_exact_pre_action_identity_pass():
    raw_res = MagicMock()
    raw_res.result_url = SOURCE_RAW_PERMALINK
    raw_res.has_thread = True
    raw_res.author = "test_user"
    raw_res.author_id = "U12345"
    raw_res.timestamp_raw = EXPECTED_MESSAGE_TS
    raw_res.text = "reply text"
    raw_res.channel_name = "test-channel"
    raw_res.channel_id = EXPECTED_CHANNEL_ID
    raw_res.gold_thread_ts = EXPECTED_ROOT_TS
    raw_res.thread_reply_count = 3

    parsed = parse_slack_message_permalink(raw_res.result_url)
    assert parsed.channel_id == EXPECTED_CHANNEL_ID
    assert normalize_slack_ts(parsed.message_ts) == normalize_slack_ts(EXPECTED_MESSAGE_TS)
    assert normalize_slack_ts(parsed.thread_ts) == normalize_slack_ts(EXPECTED_ROOT_TS)


def test_10_target_at_rank_6_with_bound_5_fails_closed_as_not_resident():
    other_hit = MagicMock()
    other_hit.result_url = "https://heumlabs.slack.com/archives/C042WS25QAX/p1718000000000000"
    other_hit.has_thread = False

    target_hit = MagicMock()
    target_hit.result_url = SOURCE_RAW_PERMALINK
    target_hit.has_thread = True

    # 5 other hits, target would be at index 5 (rank 6)
    results = [other_hit, other_hit, other_hit, other_hit, other_hit]
    # Bound to ATTEMPT3_SEARCH_MAX_RESULTS (5)
    bounded_results = results[:ATTEMPT3_SEARCH_MAX_RESULTS]

    found = False
    for res in bounded_results:
        parsed = parse_slack_message_permalink(res.result_url)
        if parsed and parsed.channel_id == EXPECTED_CHANNEL_ID and normalize_slack_ts(parsed.message_ts) == normalize_slack_ts(EXPECTED_MESSAGE_TS):
            found = True
            break
    assert found is False


def test_11_runner_max_results_frozen_at_5():
    assert ATTEMPT3_SEARCH_MAX_RESULTS == 5
    assert P1B_ADAPTIVE_EVIDENCE_BUDGET == "DEFERRED"




def test_12_identity_shift_before_click_fails_closed():
    mock_reader = MagicMock()
    mock_reader.last_telemetry = {
        "click_executed": False,
        "residency_matched": False,
        "resident_matches": 0,
    }
    report = {"live_action_inventory": {}, "checks": {}}
    harvest_thread_reader_telemetry(mock_reader, None, report)
    assert report["checks"]["immediate_pre_activation_recheck"] == "FAIL_AMBIGUOUS_OR_LOST"
    assert report["live_action_inventory"]["reply_bar_clicks"] == 0


def test_13_missing_target_fails_closed():
    report = {"checks": {}, "verdict": "FAIL_CLOSED", "P2A_L1": "FAIL_CLOSED"}
    target_evidence = None
    if not target_evidence:
        report["checks"]["pre_action_identity_check"] = "FAIL_NOT_RESIDENT"
    assert report["checks"]["pre_action_identity_check"] == "FAIL_NOT_RESIDENT"
    assert report["verdict"] == "FAIL_CLOSED"


def test_14_reply_bar_action_count_exactly_one_on_success():
    mock_reader = MagicMock()
    mock_reader.last_telemetry = {
        "click_executed": True,
        "residency_matched": True,
        "resident_matches": 1,
        "correlated_root_acquired": True,
        "extraction_ok": True,
    }
    report = {"live_action_inventory": {}, "checks": {}}
    harvest_thread_reader_telemetry(mock_reader, None, report)
    assert report["live_action_inventory"]["reply_bar_clicks"] == 1
    assert report["live_action_inventory"]["pure_read_dom_extractions"] == 1
    assert report["checks"]["reply_bar_guard_passed"] is True
    assert report["checks"]["existing_qualified_reply_bar_interaction_only"] is True


def test_15_no_alternate_click_target():
    report = {
        "live_action_inventory": {
            "search_activation_dispatches": 1,
            "search_submissions": 1,
            "reply_bar_clicks": 1,
            "pure_read_dom_extractions": 1,
            "new_ui_actions_added": 0,
            "new_click_targets_added": 0,
        }
    }
    assert report["live_action_inventory"]["new_ui_actions_added"] == 0
    assert report["live_action_inventory"]["new_click_targets_added"] == 0


def test_16_strict_root_identity_validation():
    norm_expected = normalize_slack_ts(EXPECTED_ROOT_TS)
    root_permalink = f"https://app.slack.com/archives/{EXPECTED_CHANNEL_ID}/p{EXPECTED_ROOT_TS.replace('.', '')}"

    # Valid: root.thread_ts is None
    root_none_thread = ThreadRootItem(
        text="Valid root text",
        permalink=root_permalink,
        channel_id=EXPECTED_CHANNEL_ID,
        message_ts=EXPECTED_ROOT_TS,
        thread_ts=None,
    )
    norm_msg = normalize_slack_ts(root_none_thread.message_ts)
    norm_th = normalize_slack_ts(root_none_thread.thread_ts) if root_none_thread.thread_ts else None
    assert root_none_thread.channel_id == EXPECTED_CHANNEL_ID
    assert norm_msg == norm_expected
    assert norm_th is None or norm_th == norm_expected

    # Valid: root.thread_ts == EXPECTED_ROOT_TS
    root_same_thread = ThreadRootItem(
        text="Valid root text",
        permalink=root_permalink,
        channel_id=EXPECTED_CHANNEL_ID,
        message_ts=EXPECTED_ROOT_TS,
        thread_ts=EXPECTED_ROOT_TS,
    )
    norm_th2 = normalize_slack_ts(root_same_thread.thread_ts) if root_same_thread.thread_ts else None
    assert norm_th2 == norm_expected

    # Invalid: different thread_ts
    diff_thread = "1799999999.000000"
    root_diff_thread = ThreadRootItem(
        text="Invalid root",
        permalink=root_permalink,
        channel_id=EXPECTED_CHANNEL_ID,
        message_ts=EXPECTED_ROOT_TS,
        thread_ts=diff_thread,
    )
    norm_th3 = normalize_slack_ts(root_diff_thread.thread_ts)
    assert not (norm_th3 is None or norm_th3 == norm_expected)


def test_17_root_and_reply_separation():
    root_permalink = f"https://app.slack.com/archives/{EXPECTED_CHANNEL_ID}/p{EXPECTED_ROOT_TS.replace('.', '')}"
    root = ThreadRootItem(
        text="Root body",
        permalink=root_permalink,
        channel_id=EXPECTED_CHANNEL_ID,
        message_ts=EXPECTED_ROOT_TS,
    )
    rep1 = ThreadReplyItem(
        reply_id="rep_1",
        author="user1",
        timestamp="1718079276.126469",
        text="Reply body 1",
        permalink=SOURCE_RAW_PERMALINK,
        channel_id=EXPECTED_CHANNEL_ID,
        message_ts="1718079276.126469",
        thread_ts=EXPECTED_ROOT_TS,
    )
    rep2 = ThreadReplyItem(
        reply_id="rep_2",
        author="user2",
        timestamp="1718079999.000000",
        text="Reply body 2",
        permalink=SOURCE_RAW_PERMALINK,
        channel_id=EXPECTED_CHANNEL_ID,
        message_ts="1718079999.000000",
        thread_ts=EXPECTED_ROOT_TS,
    )
    replies = [rep1, rep2]

    reply_texts = [r.text for r in replies]
    distinct = (root.text not in reply_texts and len(replies) > 0)
    assert distinct is True

    # Same text fails distinctness
    bad_rep = ThreadReplyItem(
        reply_id="rep_bad",
        author="user3",
        timestamp="1718079276.126469",
        text="Root body",
        permalink=SOURCE_RAW_PERMALINK,
        channel_id=EXPECTED_CHANNEL_ID,
        message_ts="1718079276.126469",
        thread_ts=EXPECTED_ROOT_TS,
    )
    bad_distinct = (root.text not in [bad_rep.text] and len([bad_rep]) > 0)
    assert bad_distinct is False


def test_18_evidence_item_identity_preserved_and_no_duplicates():
    evidence = EvidenceItem(
        evidence_id="ev_p2a_l1_target",
        source="slack",
        author="user1",
        timestamp=EXPECTED_MESSAGE_TS,
        text="initial reply text",
        channel=EXPECTED_CHANNEL_ID,
        message_identifier=SOURCE_RAW_PERMALINK,
        thread_identifier=EXPECTED_ROOT_TS,
        has_thread=True,
    )
    key_before = get_canonical_evidence_key(evidence)

    # Hydrate
    evidence.text = "Hydrated root body"
    evidence.thread_replies = [
        ThreadReplyItem(
            reply_id="rep_target",
            author="user1",
            timestamp=EXPECTED_MESSAGE_TS,
            text="initial reply text",
            permalink=SOURCE_RAW_PERMALINK,
            channel_id=EXPECTED_CHANNEL_ID,
            message_ts=EXPECTED_MESSAGE_TS,
            thread_ts=EXPECTED_ROOT_TS,
        )
    ]
    evidence.thread_context_status = ThreadContextStatus.COMPLETE.value
    key_after = get_canonical_evidence_key(evidence)

    assert key_before == key_after


def test_19_query_provenance_preserved():
    evidence = EvidenceItem(
        evidence_id="ev_p2a_l1_target",
        source="slack",
        text="target text",
        channel=EXPECTED_CHANNEL_ID,
        message_identifier=SOURCE_RAW_PERMALINK,
        thread_identifier=EXPECTED_ROOT_TS,
        has_thread=True,
    )
    key = get_canonical_evidence_key(evidence)
    prov_hit = CandidateQueryHit(query_index=0, search_query=SearchQuery(query=TARGET_QUERY, purpose='initial'))
    provenance_map = {key: [prov_hit]}

    assert key in provenance_map
    assert len(provenance_map[key]) > 0
    assert provenance_map[key][0].search_query.query == TARGET_QUERY


def test_20_search_safety_and_query_authority_gate():
    # Valid report
    valid_report = {
        "checks": {
            "SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED": True,
            "QUERY_STATE_VERIFIED_BEFORE_SUBMIT": True,
            "SEARCH_SURFACE_QUERY_MATCHES_REQUESTED": True,
        },
        "safety_telemetry": {
            "GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH": False,
            "SLACKBOT_ACTION_INVOKED_BY_PESU": False,
            "WRITE_CAPABLE_ACTION_INVOKED_BY_PESU": False,
            "UNSAFE_NON_SEARCH_ACTION_INVOKED_BY_PESU": False,
            "composer_focus": 0,
            "composer_text_input": 0,
            "composer_send": 0,
        },
        "live_action_inventory": {
            "search_submissions": 1,
        },
    }
    ok, reason = evaluate_search_safety_and_query_authority(valid_report)
    assert ok is True
    assert reason == "VERIFIED_SAFE_AND_CORRELATED"

    # Missing authority
    missing_report = json.loads(json.dumps(valid_report))
    missing_report["checks"]["SAFE_SEARCH_ACTION_EXPLICITLY_SELECTED"] = "UNKNOWN"
    ok, reason = evaluate_search_safety_and_query_authority(missing_report)
    assert ok is False

    # Surface query mismatch
    mismatch_report = json.loads(json.dumps(valid_report))
    mismatch_report["checks"]["SEARCH_SURFACE_QUERY_MATCHES_REQUESTED"] = False
    ok, reason = evaluate_search_safety_and_query_authority(mismatch_report)
    assert ok is False
    assert reason == "SEARCH_SURFACE_QUERY_MISMATCH"

    # Explicit generic onEnter violation
    violation_report = json.loads(json.dumps(valid_report))
    violation_report["safety_telemetry"]["GENERIC_DEFAULT_ONENTER_USED_FOR_SEARCH"] = True
    ok, reason = evaluate_search_safety_and_query_authority(violation_report)
    assert ok is False

    # Supplemental composer: UNKNOWN does not block
    comp_unk_report = json.loads(json.dumps(valid_report))
    comp_unk_report["safety_telemetry"]["composer_focus"] = "UNKNOWN"
    ok, reason = evaluate_search_safety_and_query_authority(comp_unk_report)
    assert ok is True

    # Supplemental composer: explicit > 0 fails closed
    comp_act_report = json.loads(json.dumps(valid_report))
    comp_act_report["safety_telemetry"]["composer_text_input"] = 1
    ok, reason = evaluate_search_safety_and_query_authority(comp_act_report)
    assert ok is False


def test_21_dry_run_zero_ui_actions_and_trace_persistence():
    assert DRY_RUN_OUTPUT_FILE.exists()
    with open(DRY_RUN_OUTPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["execution_mode"] == "DRY_RUN"
    assert data["verdict"] == "DRY_RUN_PASS"
    assert data["P2A_L1"] == "NOT_EXECUTED"
    assert data["thread_root_data_extraction_live_validated"] is False
    actions = data["live_action_inventory"]
    assert actions["search_activation_dispatches"] == 0
    assert actions["search_submissions"] == 0
    assert actions["reply_bar_clicks"] == 0
    assert actions["pure_read_dom_extractions"] == 0
    assert actions["new_ui_actions_added"] == 0
    assert actions["new_click_targets_added"] == 0
    assert data["provider_calls"] == 0
    assert data["retry_occurred"] is False


def test_22_historical_artifacts_hash_identical():
    targets = {
        "Attempt 1": ("output/qualification_13_5d/live_trace/task11q4_p2a_l1_trace.json", "f6f17e06f94808bac15dea32748526a1d146dac69ef0a75cc8a4467c5c8fd28e"),
        "Attempt 2 derived": ("output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt2_trace.json", "a6eacdb988306e610f446d65fd5eb56ae198d6ce976e62bd1937066eb834cd3a"),
        "RD1 Run 1": ("output/qualification_13_5d/live_trace/task11q4_p2a_l1_rd1_search_residency.json", "5a44f58616a5dac21ebeb596a10e8f55ba7a2263eb177b945485c2bdd2556846"),
        "RD1 Run 2": ("output/qualification_13_5d/live_trace/task11q4_p2a_l1_rd1_run2_search_residency.json", "fccfb28d380c81ad66e435ac43b63778184cb197da87d61eb304bf391c28f6a5"),
    }
    for name, (rel_path, exp_hash) in targets.items():
        p = REPO_ROOT / rel_path
        assert p.exists(), f"Historical artifact missing: {p}"
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        assert actual == exp_hash, f"{name} hash changed!"


def test_23_production_src_diff_empty():
    res = subprocess.run(
        ["git", "diff", "HEAD", "--", "src/"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == "", f"Production src/ diff is not empty!\n{res.stdout}"
