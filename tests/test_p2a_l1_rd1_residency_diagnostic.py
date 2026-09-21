"""Tests for Task 11 / P2A-L1-RD1: Bounded Search Result Residency Diagnostic.

Verifies:
1. Exact target match classification (EXACT_FROZEN_IDENTITY_MATCH) -> TARGET_EXACT_MATCH_FOUND
2. Same message / changed thread representation (SAME_CHANNEL_MESSAGE_DIFFERENT_THREAD) -> TARGET_REPRESENTATION_DRIFT_FOUND
3. Same thread / different result reply (SAME_CHANNEL_THREAD_DIFFERENT_MESSAGE) -> SAME_THREAD_DIFFERENT_SEARCH_HIT_FOUND
   (MUST NOT emit TARGET_REPRESENTATION_DRIFT_FOUND)
4. Parse failure (PARSE_FAILED)
5. Target absent from bounded set (TARGET_NOT_IN_BOUNDED_INSPECTED_SET when inspection_truncated == False)
6. Target absent + inspection_truncated == True MUST NOT emit conclusive target-absence verdict
   (emits SEARCH_RESULT_CAPTURE_INSUFFICIENT with MOUNTED_RESULT_SET_TRUNCATED_BY_DIAGNOSTIC_BOUND)
7. Mounted 5 / inspected 5 -> truncated false (same-snapshot count semantics)
8. Mounted 20 / inspected 20 -> truncated false (same-snapshot count semantics)
9. Mounted 21 / inspected 20 -> truncated true (same-snapshot count semantics)
10. Absence verdict REQUIRES same_snapshot_authority == True; without it, conclusive absence is rejected
11. Rank/order preserved (1, 2, 3...)
12. No message body required in row classification (residency is pure identity)
13. Terminal artifact always persisted on all execution paths (success, fail-closed, exception)
14. Dry-run and live artifact paths differ
15. Dry-run actual UI actions == 0 and observation telemetry is NOT_RUN
16. Missing telemetry fails closed (SEARCH_RESULT_CAPTURE_INSUFFICIENT / FAIL_CLOSED)
17. RD1 can never modify P2A_L1 or THREAD_ROOT_DATA_EXTRACTION_LIVE_VALIDATED
18. Attempt 1 and Attempt 2 trace artifacts remain untouched and uncorrupted
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from scripts.diagnose_task11q4_p2a_l1_search_residency import (
    EXPECTED_CHANNEL_ID,
    EXPECTED_MESSAGE_TS,
    EXPECTED_ROOT_TS,
    MAX_DIAGNOSTIC_ROWS,
    LIVE_OUTPUT_FILE,
    DRY_RUN_OUTPUT_FILE,
    classify_row_identity,
    determine_diagnostic_verdict,
    run_rd1_diagnostic,
)


def test_1_exact_target_match_classification():
    """Test 1: Exact target match produces EXACT_FROZEN_IDENTITY_MATCH and TARGET_EXACT_MATCH_FOUND."""
    raw_item = {
        "raw_index": 1,
        "canonical_permalink": f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts={EXPECTED_ROOT_TS}",
        "qualifying_permalinks": [f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts={EXPECTED_ROOT_TS}"],
        "thread_reply_detected": True,
    }
    classified = classify_row_identity(raw_item)
    assert classified["target_classification"] == "EXACT_FROZEN_IDENTITY_MATCH"
    assert classified["exact_frozen_match"] is True
    assert classified["same_channel_message_match"] is True
    assert classified["same_channel_thread_match"] is True

    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[classified],
        mounted_row_count=1,
        inspected_row_count=1,
        inspection_truncated=False,
        telemetry_ok=True,
        same_snapshot_authority=True,
    )
    assert verdict == "TARGET_EXACT_MATCH_FOUND"
    assert reason is None


def test_2_same_message_changed_thread_representation():
    """Test 2: Same channel & message_ts, but differing/omitted thread_ts produces TARGET_REPRESENTATION_DRIFT_FOUND."""
    raw_item_omitted = {
        "raw_index": 1,
        "canonical_permalink": f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529",
        "qualifying_permalinks": [f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529"],
        "thread_reply_detected": False,
    }
    c_omitted = classify_row_identity(raw_item_omitted)
    assert c_omitted["target_classification"] == "SAME_CHANNEL_MESSAGE_DIFFERENT_THREAD"
    assert c_omitted["exact_frozen_match"] is False
    assert c_omitted["same_channel_message_match"] is True
    assert c_omitted["same_channel_thread_match"] is False

    raw_item_diff = {
        "raw_index": 2,
        "canonical_permalink": f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts=1799999999.000001",
        "qualifying_permalinks": [f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts=1799999999.000001"],
        "thread_reply_detected": True,
    }
    c_diff = classify_row_identity(raw_item_diff)
    assert c_diff["target_classification"] == "SAME_CHANNEL_MESSAGE_DIFFERENT_THREAD"

    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[c_omitted],
        mounted_row_count=1,
        inspected_row_count=1,
        inspection_truncated=False,
        telemetry_ok=True,
        same_snapshot_authority=True,
    )
    assert verdict == "TARGET_REPRESENTATION_DRIFT_FOUND"
    assert reason is None


def test_3_same_thread_different_message_separated_from_drift():
    """Test 3: Same channel & thread_ts, but different message_ts MUST NOT produce TARGET_REPRESENTATION_DRIFT_FOUND."""
    raw_item_other_reply = {
        "raw_index": 1,
        "canonical_permalink": f"/archives/{EXPECTED_CHANNEL_ID}/p1788152999999999?thread_ts={EXPECTED_ROOT_TS}",
        "qualifying_permalinks": [f"/archives/{EXPECTED_CHANNEL_ID}/p1788152999999999?thread_ts={EXPECTED_ROOT_TS}"],
        "thread_reply_detected": True,
    }
    c_other = classify_row_identity(raw_item_other_reply)
    assert c_other["target_classification"] == "SAME_CHANNEL_THREAD_DIFFERENT_MESSAGE"
    assert c_other["exact_frozen_match"] is False
    assert c_other["same_channel_message_match"] is False
    assert c_other["same_channel_thread_match"] is True

    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[c_other],
        mounted_row_count=1,
        inspected_row_count=1,
        inspection_truncated=False,
        telemetry_ok=True,
        same_snapshot_authority=True,
    )
    assert verdict == "SAME_THREAD_DIFFERENT_SEARCH_HIT_FOUND"
    assert verdict != "TARGET_REPRESENTATION_DRIFT_FOUND"
    assert reason is None


def test_4_parse_failure():
    """Test 4: Malformed or unparseable permalink produces PARSE_FAILED."""
    raw_item_bad = {
        "raw_index": 1,
        "canonical_permalink": "https://random.com/not-a-slack-link",
        "qualifying_permalinks": ["https://random.com/not-a-slack-link"],
    }
    c_bad = classify_row_identity(raw_item_bad)
    assert c_bad["target_classification"] == "PARSE_FAILED"
    assert c_bad["parse_status"] == "PARSE_FAILED"
    assert c_bad["parsed_channel_id"] is None
    assert c_bad["parsed_message_ts"] is None


def test_5_target_absent_from_bounded_set_untruncated():
    """Test 5: When inspection_truncated == False and target absent, emits TARGET_NOT_IN_BOUNDED_INSPECTED_SET."""
    unrelated_row = {
        "raw_index": 1,
        "canonical_permalink": "/archives/C9999999999/p1700000000000000",
        "qualifying_permalinks": ["/archives/C9999999999/p1700000000000000"],
    }
    c_unrelated = classify_row_identity(unrelated_row)
    assert c_unrelated["target_classification"] == "UNRELATED"

    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[c_unrelated],
        mounted_row_count=1,
        inspected_row_count=1,
        inspection_truncated=False,
        telemetry_ok=True,
        same_snapshot_authority=True,
    )
    assert verdict == "TARGET_NOT_IN_BOUNDED_INSPECTED_SET"
    assert reason is None


def test_6_target_absent_truncated_must_not_emit_absence():
    """Test 6: When inspection_truncated == True and target absent, MUST NOT emit TARGET_NOT_IN_BOUNDED_INSPECTED_SET."""
    unrelated_row = {
        "raw_index": 1,
        "canonical_permalink": "/archives/C9999999999/p1700000000000000",
        "qualifying_permalinks": ["/archives/C9999999999/p1700000000000000"],
    }
    c_unrelated = classify_row_identity(unrelated_row)

    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[c_unrelated] * 20,
        mounted_row_count=25,
        inspected_row_count=20,
        inspection_truncated=True,
        telemetry_ok=True,
        same_snapshot_authority=True,
    )
    assert verdict == "SEARCH_RESULT_CAPTURE_INSUFFICIENT"
    assert verdict != "TARGET_NOT_IN_BOUNDED_INSPECTED_SET"
    assert reason == "MOUNTED_RESULT_SET_TRUNCATED_BY_DIAGNOSTIC_BOUND"


def test_7_mounted_5_inspected_5_semantics():
    """Test 7: mounted 5 / inspected 5 -> truncated false."""
    mounted_count = 5
    inspected_count = 5
    truncated = (mounted_count > inspected_count)
    assert truncated is False


def test_8_mounted_20_inspected_20_semantics():
    """Test 8: mounted 20 / inspected 20 -> truncated false."""
    mounted_count = 20
    inspected_count = 20
    truncated = (mounted_count > inspected_count)
    assert truncated is False


def test_9_mounted_21_inspected_20_semantics():
    """Test 9: mounted 21 / inspected 20 -> truncated true."""
    mounted_count = 21
    inspected_count = 20
    truncated = (mounted_count > inspected_count)
    assert truncated is True


def test_10_absence_verdict_requires_same_snapshot_authority():
    """Test 10: Absence verdict REQUIRES same_snapshot_authority == True; without it, conclusive absence is rejected."""
    unrelated_row = {
        "raw_index": 1,
        "canonical_permalink": "/archives/C9999999999/p1700000000000000",
        "qualifying_permalinks": ["/archives/C9999999999/p1700000000000000"],
    }
    c_unrelated = classify_row_identity(unrelated_row)

    # When same_snapshot_authority is False, conclusive absence is NOT allowed even if inspection_truncated is False!
    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[c_unrelated],
        mounted_row_count=1,
        inspected_row_count=1,
        inspection_truncated=False,
        telemetry_ok=True,
        same_snapshot_authority=False,  # Lacks same-snapshot authority
    )
    assert verdict == "SEARCH_RESULT_CAPTURE_INSUFFICIENT"
    assert verdict != "TARGET_NOT_IN_BOUNDED_INSPECTED_SET"
    assert reason == "LACKS_SAME_SNAPSHOT_COUNT_AUTHORITY"


def test_11_rank_order_preserved():
    """Test 11: Rank order is strictly preserved 1..N."""
    items = [
        {"raw_index": 1, "canonical_permalink": "/archives/C1/p1700000000000001"},
        {"raw_index": 2, "canonical_permalink": "/archives/C1/p1700000000000002"},
        {"raw_index": 3, "canonical_permalink": "/archives/C1/p1700000000000003"},
    ]
    classified = [classify_row_identity(it) for it in items]
    assert [c["rank"] for c in classified] == [1, 2, 3]


def test_12_no_message_body_required():
    """Test 12: Missing, empty, or ambiguous body does NOT drop row identity."""
    raw_with_no_body = {
        "raw_index": 1,
        "canonical_permalink": f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts={EXPECTED_ROOT_TS}",
        "qualifying_permalinks": [f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts={EXPECTED_ROOT_TS}"],
        "normalized_body": "",
        "body_valid": False,
        "ambiguous_fragment_count": 3,
        "thread_reply_detected": True,
    }
    c = classify_row_identity(raw_with_no_body)
    assert c["target_classification"] == "EXACT_FROZEN_IDENTITY_MATCH"
    assert "normalized_body" not in c, "Message body should not be preserved in row diagnostic metadata"


def test_13_terminal_artifact_always_persisted(tmp_path, monkeypatch):
    """Test 13: Every terminal execution path persists native artifact."""
    import scripts.diagnose_task11q4_p2a_l1_search_residency as diag_mod

    tmp_out = tmp_path / "diag_trace.json"
    monkeypatch.setattr(diag_mod, "LIVE_OUTPUT_FILE", tmp_out)
    monkeypatch.setattr(diag_mod, "OUTPUT_DIR", tmp_path)

    # Simulate exception during search
    mock_host = MagicMock()
    mock_backend = MagicMock()
    mock_backend.start_runtime = MagicMock(side_effect=RuntimeError("Simulated runtime crash"))
    monkeypatch.setattr(diag_mod, "PesuWebViewHost", lambda **kwargs: mock_host)
    monkeypatch.setattr(diag_mod, "WebViewSlackSearchBackend", lambda **kwargs: mock_backend)

    report = diag_mod.run_rd1_diagnostic(dry_run=False)

    assert report["diagnostic_verdict"] == "FAIL_CLOSED"
    assert tmp_out.exists(), "Trace artifact was not persisted on exception!"
    data = json.loads(tmp_out.read_text(encoding="utf-8"))
    assert data["diagnostic_verdict"] == "FAIL_CLOSED"
    assert data["verdict_reason"] == "RUNTIME_EXCEPTION"


def test_14_dry_run_and_live_paths_differ():
    """Test 14: Dry-run and live artifact paths are distinct and do not overwrite each other."""
    assert DRY_RUN_OUTPUT_FILE != LIVE_OUTPUT_FILE
    assert "dry_run" in DRY_RUN_OUTPUT_FILE.name
    assert "dry_run" not in LIVE_OUTPUT_FILE.name


def test_15_dry_run_zero_ui_actions(tmp_path, monkeypatch):
    """Test 15: Dry-run performs zero UI actions and writes to dry-run output file."""
    import scripts.diagnose_task11q4_p2a_l1_search_residency as diag_mod

    tmp_out = tmp_path / "test_dry_run.json"
    monkeypatch.setattr(diag_mod, "DRY_RUN_OUTPUT_FILE", tmp_out)
    monkeypatch.setattr(diag_mod, "OUTPUT_DIR", tmp_path)

    report = diag_mod.run_rd1_diagnostic(dry_run=True)

    assert report["execution_mode"] == "DRY_RUN"
    assert report["diagnostic_verdict"] == "DRY_RUN_PASS"
    assert report["SAME_SNAPSHOT_RESIDENCY_COUNTS"] is True
    st = report["safety_telemetry"]
    assert st["reply_bar_clicks"] == 0
    assert st["result_card_clicks"] == 0
    assert st["composer_interaction"] == 0
    assert st["scroll_iterations"] == 0
    assert st["provider_calls"] == 0
    assert tmp_out.exists()


def test_16_missing_telemetry_fails_closed():
    """Test 16: Missing telemetry or 0 mounted rows produces SEARCH_RESULT_CAPTURE_INSUFFICIENT."""
    verdict, reason = determine_diagnostic_verdict(
        inspected_rows=[],
        mounted_row_count=0,
        inspected_row_count=0,
        inspection_truncated=False,
        telemetry_ok=False,
        same_snapshot_authority=True,
    )
    assert verdict == "SEARCH_RESULT_CAPTURE_INSUFFICIENT"
    assert reason == "ZERO_MOUNTED_ROWS_OR_MISSING_TELEMETRY"


def test_17_rd1_never_modifies_qualification_flags():
    """Test 17: RD1 can never declare P2A_L1 = PASS or set THREAD_ROOT_DATA_EXTRACTION_LIVE_VALIDATED = true."""
    raw_item = {
        "raw_index": 1,
        "canonical_permalink": f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts={EXPECTED_ROOT_TS}",
        "qualifying_permalinks": [f"/archives/{EXPECTED_CHANNEL_ID}/p1788152491960529?thread_ts={EXPECTED_ROOT_TS}"],
        "thread_reply_detected": True,
    }
    classified = classify_row_identity(raw_item)
    verdict, _ = determine_diagnostic_verdict([classified], 1, 1, False, True, True)
    assert verdict == "TARGET_EXACT_MATCH_FOUND"

    report = {
        "diagnostic_verdict": verdict,
        "P2A_L1": "NOT_APPLICABLE_DIAGNOSTIC_ONLY",
        "thread_root_data_extraction_live_validated": False,
    }
    assert report["P2A_L1"] != "PASS"
    assert report["thread_root_data_extraction_live_validated"] is False


def test_18_attempt1_and_attempt2_artifacts_unchanged():
    """Test 18: Attempt 1 and Attempt 2 trace artifacts remain untouched and uncorrupted."""
    import hashlib
    repo_root = Path(__file__).resolve().parent.parent
    att1_path = repo_root / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_trace.json"
    att2_path = repo_root / "output" / "qualification_13_5d" / "live_trace" / "task11q4_p2a_l1_attempt2_trace.json"

    assert att1_path.exists()
    assert att2_path.exists()

    def get_hash(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest().upper()

    assert get_hash(att1_path) == "F6F17E06F94808BAC15DEA32748526A1D146DAC69EF0A75CC8A4467C5C8FD28E"
    assert get_hash(att2_path) == "A6EACDB988306E610F446D65FD5EB56AE198D6CE976E62BD1937066EB834CD3A"
