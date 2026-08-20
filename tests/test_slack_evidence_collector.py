"""Unit tests for SlackEvidenceCollector (MVP 3.2)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pesu_agent.adapters.slack_cdp import SlackCdpAdapter
from pesu_agent.evidence.slack_evidence_collector import (
    SlackEvidenceCollector,
    SlackEvidenceItem,
    SlackEvidencePackage,
)
from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
    SlackStatusResult,
)
from pesu_agent.search.slack_search import SlackSearchResult, SlackSearchSession


@pytest.fixture
def mock_lifecycle_ready():
    manager = MagicMock(spec=SlackLifecycleManager)
    manager.get_status.return_value = SlackStatusResult(
        status=SlackAgentModeStatus.AGENT_READY,
        message="Ready",
        pid=1234,
        port=9222,
        owning_process_match=True,
    )
    return manager


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """테스트 시 불필요한 실시간 asyncio.sleep 지연을 0초로 가속합니다."""
    async def noop_sleep(*args, **kwargs):
        return None
    monkeypatch.setattr("asyncio.sleep", noop_sleep)
    monkeypatch.setattr("pesu_agent.evidence.slack_evidence_collector.asyncio.sleep", noop_sleep)
    monkeypatch.setattr("pesu_agent.context.slack_context_collector.asyncio.sleep", noop_sleep)


@pytest.mark.anyio
async def test_collect_evidence_package_success_and_deduplication(mock_lifecycle_ready):
    """1. 복수 결과 조사, 단일 상태 복원, 및 글로벌 중복 제거 검증."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)

    snap_before = {
        "url": "https://app.slack.com/client/T123/C_START",
        "channel_id": "C_START",
        "conversation_name": "시작채널",
        "scroll_top": 0,
        "visible_message_fingerprints": ["fp_start"],
    }

    # Result 1 DOM: Channel A with overlapping msg "공통 메시지"
    dom_result_1 = {
        "target_idx": 1,
        "messages": [
            {"idx": 0, "author": "홍길동", "timestamp_raw": "10:00", "text": "공통 메시지", "href": "https://slack.com/p111", "has_thread": False, "reply_count": 0, "item_key": ""},
            {"idx": 1, "author": "김철수", "timestamp_raw": "10:05", "text": "타깃 1", "href": "https://slack.com/p1786524317769949", "has_thread": True, "reply_count": 1, "item_key": "p1786524317769949"},
            {"idx": 2, "author": "이영희", "timestamp_raw": "10:10", "text": "후속 메시지", "href": "https://slack.com/p222", "has_thread": False, "reply_count": 0, "item_key": ""},
        ],
    }

    # Thread DOM for Result 1
    thread_dom_result_1 = {
        "found": True,
        "messages": [
            {"idx": 0, "author": "김철수", "timestamp_raw": "10:05", "text": "타깃 1"},
            {"idx": 1, "author": "박댓글", "timestamp_raw": "10:06", "text": "스레드 댓글입니다."},
        ],
    }

    # Result 2 DOM: Same Channel with "공통 메시지" and "타깃 2"
    dom_result_2 = {
        "target_idx": 1,
        "messages": [
            {"idx": 0, "author": "홍길동", "timestamp_raw": "10:00", "text": "공통 메시지", "href": "https://slack.com/p111", "has_thread": False, "reply_count": 0, "item_key": ""},
            {"idx": 1, "author": "이영희", "timestamp_raw": "10:10", "text": "타깃 2", "href": "https://slack.com/p1786524317769950", "has_thread": False, "reply_count": 0, "item_key": "p1786524317769950"},
        ],
    }

    snap_after_restored = {
        "url": "https://app.slack.com/client/T123/C_START",
        "channel_id": "C_START",
        "conversation_name": "시작채널",
        "scroll_top": 0,
        "visible_message_fingerprints": ["fp_start"],
    }

    mock_cdp.evaluate_js = AsyncMock(side_effect=[
        snap_before,          # 1. capture before
        None,                 # 2. nav to result 1
        dom_result_1,         # 3. extract result 1
        {"clicked": True},    # 4. open thread
        thread_dom_result_1,  # 5. extract thread
        None,                 # 6. nav to result 2
        dom_result_2,         # 7. extract result 2
        None,                 # 8. nav back to start url in restore_view_state
        snap_after_restored,  # 9. capture after in restore_view_state
    ])

    results = [
        SlackSearchResult(
            result_index=0, query="수임", author="김철수", timestamp_raw="10:05", text="타깃 1",
            channel_name="채널A", channel_id="C_CHAN_A",
            result_url="https://workspace.slack.com/archives/C_CHAN_A/p1786524317769949", message_fingerprint="fp1"
        ),
        SlackSearchResult(
            result_index=1, query="수임", author="이영희", timestamp_raw="10:10", text="타깃 2",
            channel_name="채널A", channel_id="C_CHAN_A",
            result_url="https://workspace.slack.com/archives/C_CHAN_A/p1786524317769950", message_fingerprint="fp2"
        ),
    ]

    search_session = SlackSearchSession(
        requested_query="수임",
        observed_query="수임",
        searched_at="2026-08-20T16:00:00",
        query_verified=True,
        result_freshness_verified=True,
        result_signature="sig_test_1",
        result_count=len(results),
        unique_result_count=len(results),
        results=results,
    )

    collector = SlackEvidenceCollector(lifecycle_manager=mock_lifecycle_ready, cdp_adapter=mock_cdp)

    with patch.object(collector.searcher, "search", new_callable=AsyncMock, return_value=search_session), \
         patch("pesu_agent.evidence.slack_evidence_collector.is_slack_foreground", return_value=False), \
         patch("pesu_agent.context.slack_context_collector.is_slack_foreground", return_value=False):

        package: SlackEvidencePackage = await collector.collect_evidence_package(
            query="수임", max_results=2, context_before=5, context_after=5
        )

    assert package.query == "수임"
    assert package.user_state_snapshots == 1
    assert package.user_state_restore_attempts == 1
    assert package.result_metadata_snapshotted_count == 2
    assert package.results_investigated == 2
    assert package.results_succeeded == 2
    assert package.results_failed == 0
    assert package.state_restore_succeeded is True
    assert package.restore_pending is False

    # Global Deduplication check: "공통 메시지" is in both result 1 and result 2, so duplicates > 0
    assert package.duplicate_context_messages_removed >= 1

    # Thread check on Result 1
    ev1 = package.evidence_items[0]
    assert ev1.has_thread is True
    assert len(ev1.thread_replies) == 1
    assert ev1.thread_replies[0].author == "박댓글"
    assert len(ev1.thread_reply_refs) == 1
    assert ev1.target_message_ref is not None


@pytest.mark.anyio
async def test_collect_evidence_package_fault_isolation(mock_lifecycle_ready):
    """2. 개별 검색 결과 실패 시 다른 결과에 영향을 주지 않고 격리(Fault Isolation) 검증."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)

    snap_before = {
        "url": "https://app.slack.com/client/T123/C_START",
        "channel_id": "C_START",
        "conversation_name": "시작채널",
        "scroll_top": 0,
        "visible_message_fingerprints": ["fp_start"],
    }

    # Result 1: Success
    dom_result_1 = {
        "target_idx": 0,
        "messages": [
            {"idx": 0, "author": "김철수", "timestamp_raw": "10:05", "text": "타깃 1", "href": "https://slack.com/p1786524317769949", "has_thread": False, "reply_count": 0, "item_key": "p1786524317769949"},
        ],
    }

    # Result 2: Target mismatch (target_idx = -1)
    dom_result_2 = {
        "target_idx": -1,
        "messages": [
            {"idx": 0, "author": "엉뚱한사람", "timestamp_raw": "11:00", "text": "다른글", "href": "https://slack.com/p999", "has_thread": False, "reply_count": 0, "item_key": ""},
        ],
    }

    # Result 3: Success
    dom_result_3 = {
        "target_idx": 0,
        "messages": [
            {"idx": 0, "author": "박영수", "timestamp_raw": "12:00", "text": "타깃 3", "href": "https://slack.com/p1786524317769951", "has_thread": False, "reply_count": 0, "item_key": "p1786524317769951"},
        ],
    }

    snap_after_restored = {
        "url": "https://app.slack.com/client/T123/C_START",
        "channel_id": "C_START",
        "conversation_name": "시작채널",
        "scroll_top": 0,
        "visible_message_fingerprints": ["fp_start"],
    }

    mock_cdp.evaluate_js = AsyncMock(side_effect=[
        snap_before,          # 1. capture before
        None,                 # 2. nav to result 1
        dom_result_1,         # 3. extract result 1
        None,                 # 4. nav to result 2
        dom_result_2,         # 5. extract result 2 (fails match)
        None,                 # 6. nav to result 3
        dom_result_3,         # 7. extract result 3
        None,                 # 8. nav back to start url
        snap_after_restored,  # 9. capture after
    ])

    results = [
        SlackSearchResult(
            result_index=0, query="수임", author="김철수", timestamp_raw="10:05", text="타깃 1",
            channel_name="채널A", channel_id="C_CHAN_A",
            result_url="https://workspace.slack.com/archives/C_CHAN_A/p1786524317769949", message_fingerprint="fp1"
        ),
        SlackSearchResult(
            result_index=1, query="수임", author="엉뚱한사람", timestamp_raw="11:00", text="타깃 2",
            channel_name="채널B", channel_id="C_CHAN_B",
            result_url="https://workspace.slack.com/archives/C_CHAN_B/p1786524317769950", message_fingerprint="fp2"
        ),
        SlackSearchResult(
            result_index=2, query="수임", author="박영수", timestamp_raw="12:00", text="타깃 3",
            channel_name="채널C", channel_id="C_CHAN_C",
            result_url="https://workspace.slack.com/archives/C_CHAN_C/p1786524317769951", message_fingerprint="fp3"
        ),
    ]

    search_session = SlackSearchSession(
        requested_query="수임",
        observed_query="수임",
        searched_at="2026-08-20T16:00:00",
        query_verified=True,
        result_freshness_verified=True,
        result_signature="sig_test_2",
        result_count=len(results),
        unique_result_count=len(results),
        results=results,
    )

    collector = SlackEvidenceCollector(lifecycle_manager=mock_lifecycle_ready, cdp_adapter=mock_cdp)

    with patch.object(collector.searcher, "search", new_callable=AsyncMock, return_value=search_session), \
         patch("pesu_agent.evidence.slack_evidence_collector.is_slack_foreground", return_value=False), \
         patch("pesu_agent.context.slack_context_collector.is_slack_foreground", return_value=False):

        package = await collector.collect_evidence_package(
            query="수임", max_results=3, context_before=5, context_after=5
        )

    assert package.results_investigated == 3
    assert package.results_succeeded == 2
    assert package.results_failed == 1
    assert package.evidence_items[0].target_verified is True
    assert package.evidence_items[1].target_verified is False
    assert "찾을 수 없음" in package.evidence_items[1].failure_reason
    assert package.evidence_items[2].target_verified is True


@pytest.mark.anyio
async def test_collect_evidence_package_user_interruption_restore_succeeds_on_bg(mock_lifecycle_ready):
    """3. 다중 조사 중 사용자가 Slack 활성화 시 즉시 중단 -> Background 복귀 후 1회 복원 성공 검증."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)

    snap_before = {
        "url": "https://app.slack.com/client/T123/C_START",
        "channel_id": "C_START",
        "conversation_name": "시작채널",
        "scroll_top": 0,
        "visible_message_fingerprints": ["fp_start"],
    }

    dom_result_1 = {
        "target_idx": 0,
        "messages": [
            {"idx": 0, "author": "김철수", "timestamp_raw": "10:05", "text": "타깃 1", "href": "https://slack.com/p1786524317769949", "has_thread": False, "reply_count": 0, "item_key": "p1786524317769949"},
        ],
    }

    snap_after_restored = {
        "url": "https://app.slack.com/client/T123/C_START",
        "channel_id": "C_START",
        "conversation_name": "시작채널",
        "scroll_top": 0,
        "visible_message_fingerprints": ["fp_start"],
    }

    mock_cdp.evaluate_js = AsyncMock(side_effect=[
        snap_before,          # 1. capture before
        None,                 # 2. nav to result 1
        dom_result_1,         # 3. extract result 1
        None,                 # 4. nav back to start url on interrupt restoration
        snap_after_restored,  # 5. capture after
    ])

    results = [
        SlackSearchResult(
            result_index=0, query="수임", author="김철수", timestamp_raw="10:05", text="타깃 1",
            channel_name="채널A", channel_id="C_CHAN_A",
            result_url="https://workspace.slack.com/archives/C_CHAN_A/p1786524317769949", message_fingerprint="fp1"
        ),
        SlackSearchResult(
            result_index=1, query="수임", author="이영희", timestamp_raw="10:10", text="타깃 2",
            channel_name="채널A", channel_id="C_CHAN_A",
            result_url="https://workspace.slack.com/archives/C_CHAN_A/p1786524317769950", message_fingerprint="fp2"
        ),
    ]

    search_session = SlackSearchSession(
        requested_query="수임",
        observed_query="수임",
        searched_at="2026-08-20T16:00:00",
        query_verified=True,
        result_freshness_verified=True,
        result_signature="sig_test_3",
        result_count=len(results),
        unique_result_count=len(results),
        results=results,
    )

    collector = SlackEvidenceCollector(lifecycle_manager=mock_lifecycle_ready, cdp_adapter=mock_cdp)

    # Foreground sequence:
    # 1. before start -> False
    # 2. result 1 -> False
    # 3. result 1 after nav -> False
    # 4. result 2 before loop -> True! (User opened Slack)
    # 5. wait_for_slack_background check -> False (returned to background)
    fg_sequence = [False, False, False, True, False, False, False]
    with patch.object(collector.searcher, "search", new_callable=AsyncMock, return_value=search_session), \
         patch("pesu_agent.evidence.slack_evidence_collector.is_slack_foreground", side_effect=fg_sequence), \
         patch("pesu_agent.context.slack_context_collector.is_slack_foreground", return_value=False):

        package = await collector.collect_evidence_package(query="수임", max_results=2, bg_wait_timeout_sec=0.1)

    assert package.user_interrupted is True
    assert package.interruption_reason == "user_opened_slack"
    assert package.results_investigated == 1
    assert package.state_restore_succeeded is True
    assert package.restore_pending is False


@pytest.mark.anyio
async def test_collect_evidence_package_user_interruption_restore_pending_when_user_stays_foreground(mock_lifecycle_ready):
    """4. 다중 조사 중 사용자 활성화 후 계속 사용 중일 때(Timeout): 복원 미수행 및 restore_pending=True 검증."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)

    snap_before = {
        "url": "https://app.slack.com/client/T123/C_START",
        "channel_id": "C_START",
        "conversation_name": "시작채널",
        "scroll_top": 0,
        "visible_message_fingerprints": ["fp_start"],
    }

    dom_result_1 = {
        "target_idx": 0,
        "messages": [
            {"idx": 0, "author": "김철수", "timestamp_raw": "10:05", "text": "타깃 1", "href": "https://slack.com/p1786524317769949", "has_thread": False, "reply_count": 0, "item_key": "p1786524317769949"},
        ],
    }

    # CDP should NOT be called to restore view state because user is still foreground
    mock_cdp.evaluate_js = AsyncMock(side_effect=[
        snap_before,          # 1. capture before
        None,                 # 2. nav to result 1
        dom_result_1,         # 3. extract result 1
    ])

    results = [
        SlackSearchResult(
            result_index=0, query="수임", author="김철수", timestamp_raw="10:05", text="타깃 1",
            channel_name="채널A", channel_id="C_CHAN_A",
            result_url="https://workspace.slack.com/archives/C_CHAN_A/p1786524317769949", message_fingerprint="fp1"
        ),
        SlackSearchResult(
            result_index=1, query="수임", author="이영희", timestamp_raw="10:10", text="타깃 2",
            channel_name="채널A", channel_id="C_CHAN_A",
            result_url="https://workspace.slack.com/archives/C_CHAN_A/p1786524317769950", message_fingerprint="fp2"
        ),
    ]

    search_session = SlackSearchSession(
        requested_query="수임",
        observed_query="수임",
        searched_at="2026-08-20T16:00:00",
        query_verified=True,
        result_freshness_verified=True,
        result_signature="sig_test_4",
        result_count=len(results),
        unique_result_count=len(results),
        results=results,
    )

    collector = SlackEvidenceCollector(lifecycle_manager=mock_lifecycle_ready, cdp_adapter=mock_cdp)

    # Foreground sequence:
    # 1. before start -> False
    # 2. result 1 -> False
    # 3. result 1 after nav -> False
    # 4. result 2 before loop -> True! (User opened Slack)
    # 5. wait_for_slack_background check -> True! (User STILL looking at Slack)
    fg_sequence = [False, False, False, True, True, True, True, True]
    with patch.object(collector.searcher, "search", new_callable=AsyncMock, return_value=search_session), \
         patch("pesu_agent.evidence.slack_evidence_collector.is_slack_foreground", side_effect=fg_sequence), \
         patch("pesu_agent.context.slack_context_collector.is_slack_foreground", return_value=True):

        package = await collector.collect_evidence_package(query="수임", max_results=2, bg_wait_timeout_sec=0.1)

    assert package.user_interrupted is True
    assert package.interruption_reason == "user_opened_slack"
    assert package.restore_pending is True
    assert package.state_restore_attempted is False
    assert package.state_restore_succeeded is False
