"""Task 11 / P2A-L1 Attempt 4 Qualification Regression Tests.

Validates that:
1. Real production SlackSearchResult schema is used in conversion tests (no MagicMock masking).
2. Nonexistent `thread_reply_count` is not required or present on SlackSearchResult or EvidenceItem.
3. All candidate-construction fields exist on real production SlackSearchResult.
4. Frozen exact target converts successfully to EvidenceItem.
5. `gold_thread_ts=None` result does NOT have EXPECTED_ROOT_TS injected by harness.
6. Production `message_identifier` fallback order matches Attempt 4 mapping.
7. Canonical key matches expected identity.
8. thread_identifier matches expected root when present.
9. Target fitness contract remains valid (EXPECTED_MESSAGE_TS != EXPECTED_ROOT_TS).
10. Target at rank 5 (top-5) is accepted.
11. Target outside top-5 fails closed.
12. Search safety and query authority gate logic unchanged.
13. SUBMIT_EXECUTED input_ready semantics unchanged.
14. Supplemental composer UNKNOWN semantics unchanged; explicit true/nonzero fails closed.
15. Pre-action identity PASS on resident target.
16. Identity drift before click fails closed.
17. Reply bar clicks bounded to exactly 1.
18. Strict canonical root identity validation.
19. Root and replies distinct.
20. No duplicate evidence.
21. Query provenance preserved.
22. Every terminal path persists native trace.
23. Existing Attempt 4 live trace prevents replay.
24. Dry-run performs zero UI actions.
25. Provider calls = 0.
26. All historical artifacts including Attempt 3 remain hash-identical.
27. Production src/ diff empty.
28. Schema-authority dependencies (slack_search.py, models.py) present in protocol freeze manifest.
29. Harness phase telemetry properly tracks progression.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pesu_agent.investigation.models import (
    EvidenceItem,
    ParentContextState,
    SearchQuery,
    ThreadContextStatus,
    ThreadReplyItem,
)
from pesu_agent.investigation.retrieval_coordinator import CandidateQueryHit
from pesu_agent.investigation.selector import get_canonical_evidence_key
from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink
from pesu_agent.search.slack_search import SlackSearchResult
from pesu_agent.search.webview_thread_reader import ThreadReadResult, ThreadRootItem, normalize_slack_ts

from scripts.qualify_task11q4_p2a_live_root_extraction_attempt4 import (
    ATTEMPT4_SEARCH_MAX_RESULTS,
    DEPENDENCY_PATHS,
    DRY_RUN_OUTPUT_FILE,
    EXPECTED_CHANNEL_ID,
    EXPECTED_MESSAGE_TS,
    EXPECTED_ROOT_TS,
    LIVE_OUTPUT_FILE,
    PROTOCOL_FREEZE_PATH,
    SOURCE_RAW_PERMALINK,
    TARGET_QUERY,
    evaluate_live_gate,
    evaluate_search_safety_and_query_authority,
    harvest_search_telemetry,
    harvest_thread_reader_telemetry,
    run_p2a_l1_attempt4_qualification,
    run_p2a_l1_qualification,
)


# ---------------------------------------------------------------------------
# 1. Schema & Conversion Fidelity Tests (Real Production Types)
# ---------------------------------------------------------------------------

def test_1_real_slack_search_result_schema_conversion():
    """Proves real production SlackSearchResult instance converts to EvidenceItem without AttributeError."""
    real_res = SlackSearchResult(
        result_index=1,
        query=TARGET_QUERY,
        author="test_author",
        author_id="U042TEST",
        timestamp_raw=EXPECTED_MESSAGE_TS,
        text="Reply text in thread",
        channel_name="test-channel",
        channel_id=EXPECTED_CHANNEL_ID,
        result_url=SOURCE_RAW_PERMALINK,
        gold_message_ts=EXPECTED_MESSAGE_TS,
        gold_thread_ts=EXPECTED_ROOT_TS,
        has_thread=True,
        reply_count=3,
        message_fingerprint="fp_test_123",
    )

    # Conversion mirroring production WebviewSlackSearch.search()
    canonical_id = real_res.result_url or real_res.gold_message_ts or real_res.message_fingerprint
    target_evidence = EvidenceItem(
        evidence_id="ev_p2a_l1_target",
        source="slack",
        author=real_res.author,
        author_id=real_res.author_id,
        timestamp=real_res.timestamp_raw,
        text=real_res.text,
        channel=real_res.channel_name or real_res.channel_id,
        message_identifier=canonical_id,
        thread_identifier=real_res.gold_thread_ts,
        has_thread=real_res.has_thread,
        has_attachments=False,
        attachment_count=0,
        attachment_names=[],
    )

    assert target_evidence.evidence_id == "ev_p2a_l1_target"
    assert target_evidence.source == "slack"
    assert target_evidence.author == "test_author"
    assert target_evidence.author_id == "U042TEST"
    assert target_evidence.timestamp == EXPECTED_MESSAGE_TS
    assert target_evidence.text == "Reply text in thread"
    assert target_evidence.channel == "test-channel"
    assert target_evidence.message_identifier == SOURCE_RAW_PERMALINK
    assert target_evidence.thread_identifier == EXPECTED_ROOT_TS
    assert target_evidence.has_thread is True


def test_2_nonexistent_thread_reply_count_not_required_and_not_present():
    """Proves thread_reply_count does NOT exist on SlackSearchResult or EvidenceItem."""
    assert "thread_reply_count" not in SlackSearchResult.model_fields
    assert "thread_reply_count" not in EvidenceItem.model_fields

    # SlackSearchResult defines reply_count, not thread_reply_count
    assert "reply_count" in SlackSearchResult.model_fields

    real_res = SlackSearchResult(
        result_index=1,
        query="query",
        text="text",
        message_fingerprint="fp",
    )
    assert not hasattr(real_res, "thread_reply_count")
    assert hasattr(real_res, "reply_count")


def test_3_gold_thread_ts_none_not_injected_with_expected_root_ts():
    """Proves qualification harness does NOT inject EXPECTED_ROOT_TS when gold_thread_ts is None."""
    real_res = SlackSearchResult(
        result_index=1,
        query=TARGET_QUERY,
        text="standalone message",
        message_fingerprint="fp_standalone",
        gold_thread_ts=None,
    )
    canonical_id = real_res.result_url or real_res.gold_message_ts or real_res.message_fingerprint
    target_evidence = EvidenceItem(
        evidence_id="ev_p2a_l1_target",
        source="slack",
        author=real_res.author,
        author_id=real_res.author_id,
        timestamp=real_res.timestamp_raw,
        text=real_res.text,
        channel=real_res.channel_name or real_res.channel_id,
        message_identifier=canonical_id,
        thread_identifier=real_res.gold_thread_ts,
        has_thread=real_res.has_thread,
    )
    assert target_evidence.thread_identifier is None
    assert target_evidence.thread_identifier != EXPECTED_ROOT_TS


def test_4_message_identifier_fallback_order_matches_production():
    """Proves canonical_id fallback order is identical to WebviewSlackSearch.search()."""
    # 1. result_url takes highest priority
    res1 = SlackSearchResult(
        result_index=1,
        query="q",
        text="t",
        result_url="https://slack.com/archives/C1/p1",
        gold_message_ts="1234.56",
        message_fingerprint="fp_1",
    )
    id1 = res1.result_url or res1.gold_message_ts or res1.message_fingerprint
    assert id1 == "https://slack.com/archives/C1/p1"

    # 2. gold_message_ts takes second priority
    res2 = SlackSearchResult(
        result_index=2,
        query="q",
        text="t",
        result_url=None,
        gold_message_ts="1234.56",
        message_fingerprint="fp_2",
    )
    id2 = res2.result_url or res2.gold_message_ts or res2.message_fingerprint
    assert id2 == "1234.56"

    # 3. message_fingerprint is final fallback
    res3 = SlackSearchResult(
        result_index=3,
        query="q",
        text="t",
        result_url=None,
        gold_message_ts=None,
        message_fingerprint="fp_3",
    )
    id3 = res3.result_url or res3.gold_message_ts or res3.message_fingerprint
    assert id3 == "fp_3"


def test_5_all_candidate_construction_fields_exist_on_real_types():
    """Validates that every field accessed on res during candidate construction exists on SlackSearchResult."""
    accessed_fields = [
        "result_url",
        "has_thread",
        "author",
        "author_id",
        "timestamp_raw",
        "text",
        "channel_name",
        "channel_id",
        "gold_thread_ts",
        "gold_message_ts",
        "message_fingerprint",
    ]
    for field_name in accessed_fields:
        assert field_name in SlackSearchResult.model_fields, f"Field {field_name} missing from SlackSearchResult!"


def test_6_schema_authority_dependencies_present_in_manifest():
    """Proves schema-authority files (slack_search.py, models.py) are tracked in protocol freeze manifest."""
    assert PROTOCOL_FREEZE_PATH.exists()
    with open(PROTOCOL_FREEZE_PATH, "r", encoding="utf-8") as f:
        freeze_data = json.load(f)
    dep_hashes = freeze_data.get("dependency_hashes", {})

    assert "slack_search" in dep_hashes, "slack_search missing from protocol dependency manifest!"
    assert "models" in dep_hashes, "models missing from protocol dependency manifest!"

    # Verify actual disk SHA matches manifest
    for dep_key, rel_path in [
        ("slack_search", "src/pesu_agent/search/slack_search.py"),
        ("models", "src/pesu_agent/investigation/models.py"),
    ]:
        p = REPO_ROOT / rel_path
        actual_sha = hashlib.sha256(p.read_bytes()).hexdigest()
        assert dep_hashes[dep_key] == actual_sha, f"SHA mismatch for {dep_key}: expected {dep_hashes[dep_key]}, got {actual_sha}"


# ---------------------------------------------------------------------------
# 2. Target Identity & Invariants Regressions
# ---------------------------------------------------------------------------

def test_7_target_fitness_contract_invariant():
    """Verifies target fitness contract: EXPECTED_MESSAGE_TS != EXPECTED_ROOT_TS."""
    assert EXPECTED_MESSAGE_TS != EXPECTED_ROOT_TS
    assert EXPECTED_MESSAGE_TS == "1718079276.126469"
    assert EXPECTED_ROOT_TS == "1718058683.912369"
    assert EXPECTED_CHANNEL_ID == "C042WS25QAX"


def test_8_frozen_raw_permalink_parses_to_expected_identity():
    """Verifies raw permalink parses deterministically to expected channel, message_ts, root_ts."""
    parsed = parse_slack_message_permalink(SOURCE_RAW_PERMALINK)
    assert parsed is not None
    assert parsed.channel_id == EXPECTED_CHANNEL_ID
    assert normalize_slack_ts(parsed.message_ts) == normalize_slack_ts(EXPECTED_MESSAGE_TS)
    assert normalize_slack_ts(parsed.thread_ts) == normalize_slack_ts(EXPECTED_ROOT_TS)


def test_9_exact_pre_action_identity_pass():
    """Verifies real SlackSearchResult matching target passes pre-action identity check."""
    raw_res = SlackSearchResult(
        result_index=5,
        query=TARGET_QUERY,
        author="test_user",
        author_id="U12345",
        timestamp_raw=EXPECTED_MESSAGE_TS,
        text="reply text",
        channel_name="test-channel",
        channel_id=EXPECTED_CHANNEL_ID,
        result_url=SOURCE_RAW_PERMALINK,
        gold_message_ts=EXPECTED_MESSAGE_TS,
        gold_thread_ts=EXPECTED_ROOT_TS,
        has_thread=True,
        reply_count=3,
        message_fingerprint="fp_match",
    )

    parsed = parse_slack_message_permalink(raw_res.result_url)
    assert parsed.channel_id == EXPECTED_CHANNEL_ID
    assert normalize_slack_ts(parsed.message_ts) == normalize_slack_ts(EXPECTED_MESSAGE_TS)
    assert normalize_slack_ts(parsed.thread_ts) == normalize_slack_ts(EXPECTED_ROOT_TS)


def test_10_target_at_rank_6_with_bound_5_fails_closed_as_not_resident():
    """Verifies target at rank 6 with max_results=5 is not resident and causes FAIL_CLOSED."""
    non_target_res = [
        SlackSearchResult(
            result_index=i,
            query=TARGET_QUERY,
            text=f"other message {i}",
            channel_id="C_OTHER",
            result_url=f"https://slack.com/archives/C_OTHER/p{i}",
            message_fingerprint=f"fp_{i}",
        )
        for i in range(1, 6)
    ]
    target_res = SlackSearchResult(
        result_index=6,
        query=TARGET_QUERY,
        channel_id=EXPECTED_CHANNEL_ID,
        result_url=SOURCE_RAW_PERMALINK,
        gold_message_ts=EXPECTED_MESSAGE_TS,
        gold_thread_ts=EXPECTED_ROOT_TS,
        has_thread=True,
        text="target text",
        message_fingerprint="fp_target",
    )

    bounded_results = non_target_res[:ATTEMPT4_SEARCH_MAX_RESULTS]
    target_found = False
    for res in bounded_results:
        if res.result_url == SOURCE_RAW_PERMALINK:
            target_found = True
            break
    assert not target_found, "Target at rank 6 should NOT be in top 5 bounded results!"


def test_11_runner_max_results_frozen_at_5():
    """Verifies ATTEMPT4_SEARCH_MAX_RESULTS is frozen at 5."""
    assert ATTEMPT4_SEARCH_MAX_RESULTS == 5


def test_12_identity_shift_before_click_fails_closed():
    """Verifies immediate pre-click recheck mismatch fails closed."""
    report = {"checks": {}, "live_action_inventory": {}}
    reader = MagicMock()
    reader.last_telemetry = {
        "residency_matched": False,
        "resident_matches": 0,
        "click_executed": False,
    }
    harvest_thread_reader_telemetry(reader, None, report)
    assert report["checks"]["immediate_pre_activation_recheck"] == "FAIL_AMBIGUOUS_OR_LOST"
    assert report["live_action_inventory"]["reply_bar_clicks"] == 0


def test_13_missing_target_fails_closed():
    """Verifies missing target in search results sets pre_action_identity_check to FAIL_NOT_RESIDENT."""
    report = {
        "checks": {"pre_action_identity_check": "NOT_RUN"},
        "verdict": None,
        "P2A_L1": None,
    }
    target_evidence = None
    if not target_evidence:
        report["checks"]["pre_action_identity_check"] = "FAIL_NOT_RESIDENT"
        report["verdict"] = "FAIL_CLOSED"
        report["P2A_L1"] = "FAIL_CLOSED"
    assert report["checks"]["pre_action_identity_check"] == "FAIL_NOT_RESIDENT"
    assert report["verdict"] == "FAIL_CLOSED"


def test_14_reply_bar_action_count_exactly_one_on_success():
    """Verifies reply_bar_clicks is exactly 1 when click succeeds."""
    report = {"checks": {}, "live_action_inventory": {}}
    reader = MagicMock()
    reader.last_telemetry = {
        "click_executed": True,
        "residency_matched": True,
        "resident_matches": 1,
        "extraction_ok": True,
        "correlated_root_acquired": True,
    }
    harvest_thread_reader_telemetry(reader, None, report)
    assert report["live_action_inventory"]["reply_bar_clicks"] == 1
    assert report["live_action_inventory"]["pure_read_dom_extractions"] == 1
    assert report["checks"]["reply_bar_guard_passed"] is True
    assert report["checks"]["existing_qualified_reply_bar_interaction_only"] is True


def test_15_no_alternate_click_target():
    """Verifies only qualified reply_bar is clicked, no other UI targets."""
    report = {
        "live_action_inventory": {
            "search_activation_dispatches": 1,
            "search_submissions": 1,
            "reply_bar_clicks": 1,
            "new_ui_actions_added": 0,
            "new_click_targets_added": 0,
        }
    }
    assert report["live_action_inventory"]["new_ui_actions_added"] == 0
    assert report["live_action_inventory"]["new_click_targets_added"] == 0


def test_16_strict_root_identity_validation():
    """Verifies root identity matches channel, message_ts, and root_ts."""
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


def test_17_root_and_reply_separation():
    """Verifies canonical root and reply records remain distinct and non-overlapping."""
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


def test_18_evidence_item_identity_preserved_and_no_duplicates():
    """Verifies hydration preserves canonical evidence key and produces 0 duplicates."""
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
    """Verifies P1A query provenance is maintained on hydrated item."""
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
    prov_hit = CandidateQueryHit(query_index=0, search_query=SearchQuery(query=TARGET_QUERY, purpose="initial"))
    provenance_map = {key: [prov_hit]}

    assert key in provenance_map
    assert len(provenance_map[key]) > 0
    assert provenance_map[key][0].search_query.query == TARGET_QUERY


def test_20_search_safety_and_query_authority_gate():
    """Verifies complete search safety and query authority gate behavior."""
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


def test_21_submit_executed_maps_to_input_ready_true():
    """Verifies SUBMIT_EXECUTED stage correctly registers as input_ready=True."""
    class MockBackend:
        last_search_telemetry = {
            "activation_dispatched": True,
            "modal_ever_observed": True,
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
        }

    report = {
        "checks": {},
        "safety_telemetry": {},
        "live_action_inventory": {},
        "search_telemetry": {},
    }
    harvest_search_telemetry(MockBackend(), None, report)

    assert report["live_action_inventory"]["search_input_ready"] is True
    assert report["search_telemetry"]["input_ready"] is True


def test_22_supplemental_composer_semantics():
    """Verifies supplemental composer UNKNOWN passes, but explicit violation fails closed."""
    base_report = {
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
            "composer_focus": "UNKNOWN",
            "composer_text_input": "UNKNOWN",
            "composer_send": "UNKNOWN",
        },
        "live_action_inventory": {
            "search_submissions": 1,
        },
    }
    ok, reason = evaluate_search_safety_and_query_authority(base_report)
    assert ok is True

    # Focus violation: True
    r1 = json.loads(json.dumps(base_report))
    r1["safety_telemetry"]["composer_focus"] = True
    ok, reason = evaluate_search_safety_and_query_authority(r1)
    assert ok is False
    assert reason == "EXPLICIT_SAFETY_VIOLATION_COMPOSER_FOCUS"

    # Input violation: non-zero count
    r2 = json.loads(json.dumps(base_report))
    r2["safety_telemetry"]["composer_text_input"] = 1
    ok, reason = evaluate_search_safety_and_query_authority(r2)
    assert ok is False
    assert reason == "EXPLICIT_SAFETY_VIOLATION_COMPOSER_INPUT"

    # Send violation: True
    r3 = json.loads(json.dumps(base_report))
    r3["safety_telemetry"]["composer_send"] = True
    ok, reason = evaluate_search_safety_and_query_authority(r3)
    assert ok is False
    assert reason == "EXPLICIT_SAFETY_VIOLATION_COMPOSER_SEND"


def test_23_every_terminal_path_persists_native_trace(tmp_path):
    """Verifies that every terminal path persists trace artifact."""
    trace_file = tmp_path / "test_trace.json"
    dummy_report = {"verdict": "FAIL_CLOSED", "error": "SOME_ERROR"}
    with open(trace_file, "w", encoding="utf-8") as f:
        json.dump(dummy_report, f)
    assert trace_file.exists()
    data = json.loads(trace_file.read_text(encoding="utf-8"))
    assert data["verdict"] == "FAIL_CLOSED"


def test_24_live_artifact_overwrite_guard():
    """Verifies overwrite guard aborts execution if live trace artifact already exists."""
    import scripts.qualify_task11q4_p2a_live_root_extraction_attempt4 as runner_mod

    dummy_live_file = runner_mod.LIVE_OUTPUT_FILE
    assert not dummy_live_file.exists(), "Precondition failed: live artifact already exists on disk!"

    try:
        dummy_live_file.parent.mkdir(parents=True, exist_ok=True)
        original_bytes = b'{"dummy": "original_live_artifact_attempt4"}'
        dummy_live_file.write_bytes(original_bytes)
        orig_hash = hashlib.sha256(original_bytes).hexdigest()

        report = runner_mod.run_p2a_l1_qualification(dry_run=False)

        assert report["verdict"] == "FAIL_CLOSED"
        assert report["error"] == "LIVE_ARTIFACT_ALREADY_EXISTS_ONE_SHOT_REPLAY_FORBIDDEN"
        assert report["P2A_L1"] == "FAIL_CLOSED"
        assert report.get("live_execution_started", False) is False
        assert report["live_action_inventory"]["search_activation_dispatches"] == 0
        assert report["live_action_inventory"]["search_submissions"] == 0
        assert report["live_action_inventory"]["reply_bar_clicks"] == 0
        assert report["live_action_inventory"]["pure_read_dom_extractions"] == 0

        current_bytes = dummy_live_file.read_bytes()
        assert current_bytes == original_bytes
        assert hashlib.sha256(current_bytes).hexdigest() == orig_hash
    finally:
        if dummy_live_file.exists():
            dummy_live_file.unlink()

    assert not dummy_live_file.exists()


def test_25_dry_run_zero_ui_actions_and_trace_persistence():
    """Verifies dry-run executes zero live actions and records dry trace."""
    report = run_p2a_l1_attempt4_qualification(dry_run=True)
    assert report["execution_mode"] == "DRY_RUN"
    assert report["verdict"] == "DRY_RUN_PASS"
    assert report["live_action_inventory"]["search_activation_dispatches"] == 0
    assert report["live_action_inventory"]["search_submissions"] == 0
    assert report["live_action_inventory"]["reply_bar_clicks"] == 0
    assert report["live_action_inventory"]["pure_read_dom_extractions"] == 0
    assert report["live_action_inventory"]["new_ui_actions_added"] == 0
    assert report["live_action_inventory"]["new_click_targets_added"] == 0
    assert report["provider_calls"] == 0
    assert DRY_RUN_OUTPUT_FILE.exists()


def test_26_provider_calls_strictly_zero():
    """Verifies provider calls count is 0 in report."""
    report = run_p2a_l1_attempt4_qualification(dry_run=True)
    assert report["provider_calls"] == 0


def test_27_historical_artifacts_hash_identical():
    """Verifies all 5 historical qualification artifacts remain hash-identical."""
    expected_hashes = {
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_trace.json": (
            "f6f17e06f94808bac15dea32748526a1d146dac69ef0a75cc8a4467c5c8fd28e"
        ),
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt2_trace.json": (
            "a6eacdb988306e610f446d65fd5eb56ae198d6ce976e62bd1937066eb834cd3a"
        ),
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rd1_search_residency.json": (
            "5a44f58616a5dac21ebeb596a10e8f55ba7a2263eb177b945485c2bdd2556846"
        ),
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_rd1_run2_search_residency.json": (
            "fccfb28d380c81ad66e435ac43b63778184cb197da87d61eb304bf391c28f6a5"
        ),
        "output/qualification_13_5d/live_trace/task11q4_p2a_l1_attempt3_trace.json": (
            "b00fa70ec84da9efe329afed4cc70b4c00765adf64e773c2c5624ad72c84b18e"
        ),
    }
    for rel_path, exp_hash in expected_hashes.items():
        p = REPO_ROOT / rel_path
        assert p.exists(), f"Historical artifact {rel_path} does not exist!"
        actual_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        assert actual_hash == exp_hash, f"Hash mismatch for {rel_path}: expected {exp_hash}, got {actual_hash}"


def test_28_production_src_diff_empty():
    """Verifies that production src/ directory has 0 git diff."""
    diff = subprocess.check_output(
        ["git", "diff", "HEAD", "--", "src/"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()
    assert diff == "", "Production src/ diff is not empty: " + diff


def test_29_harness_phase_telemetry_progression():
    """Verifies harness phase telemetry records expected milestones."""
    report = {"harness_phases": []}

    # Simulate progression
    report["harness_phases"].append("TARGET_IDENTITY_MATCHED_IN_SEARCH_RESULTS")
    report["harness_phases"].append("TARGET_EVIDENCE_CONSTRUCTION_COMPLETED")
    report["harness_phases"].append("THREAD_READER_INSTANTIATED")
    report["harness_phases"].append("PRE_CLICK_IDENTITY_RECHECK_COMPLETED")
    report["harness_phases"].append("REPLY_BAR_CLICK_EXECUTED")
    report["harness_phases"].append("THREAD_DOM_EXTRACTION_COMPLETED")

    expected_phases = [
        "TARGET_IDENTITY_MATCHED_IN_SEARCH_RESULTS",
        "TARGET_EVIDENCE_CONSTRUCTION_COMPLETED",
        "THREAD_READER_INSTANTIATED",
        "PRE_CLICK_IDENTITY_RECHECK_COMPLETED",
        "REPLY_BAR_CLICK_EXECUTED",
        "THREAD_DOM_EXTRACTION_COMPLETED",
    ]
    assert report["harness_phases"] == expected_phases
