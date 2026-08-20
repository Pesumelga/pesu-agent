"""Unit tests for SlackContextCollector (MVP 3.1 & MVP 3.1.1)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pesu_agent.adapters.slack_cdp import SlackCdpAdapter
from pesu_agent.context.slack_context_collector import (
    SlackContextCollector,
    SlackMessageContext,
    SlackRestorationResult,
    SlackViewState,
)
from pesu_agent.lifecycle.slack_lifecycle import (
    SlackStatusResult,
    SlackAgentModeStatus,
    SlackLifecycleManager,
)
from pesu_agent.search.slack_search import SlackSearchResult


@pytest.fixture
def mock_lifecycle_ready():
    manager = MagicMock(spec=SlackLifecycleManager)
    manager.get_status.return_value = SlackStatusResult(
        status=SlackAgentModeStatus.AGENT_READY,
        message="Slack Agent Mode 준비 완료",
        pid=1234,
        port=9222,
        owning_process_match=True,
    )
    return manager


def test_parse_permalink():
    """1. 퍼머링크에서 channel_id와 raw_ts 및 ts_float 파싱 검증."""
    permalink = "https://myteam.slack.com/archives/C0106N97B4Q/p1786524317769949"
    chan_id, raw_ts, ts_float = SlackContextCollector.parse_permalink(permalink)
    assert chan_id == "C0106N97B4Q"
    assert raw_ts == "1786524317769949"
    assert ts_float == pytest.approx(1786524317.769949)

    # 비정상 URL
    assert SlackContextCollector.parse_permalink("") == (None, None, None)
    assert SlackContextCollector.parse_permalink("https://google.com") == (None, None, None)


@pytest.mark.anyio
async def test_capture_and_restore_view_state_full_success(mock_lifecycle_ready):
    """2. 상태 스냅샷 캡처 및 복원 성공 검증 (MVP 3.1.1)."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)

    # 1) Capture Before
    snap_before = {
        "url": "https://app.slack.com/client/T123/C_CHANNEL_A",
        "channel_id": "C_CHANNEL_A",
        "conversation_name": "일반",
        "scroll_top": 500,
        "scroll_height": 3000,
        "client_height": 800,
        "visible_message_fingerprints": ["fp1", "fp2", "fp3"],
        "first_visible_message": "첫 메시지",
        "last_visible_message": "끝 메시지",
    }

    # 2) Target DOM extraction (Channel B)
    dom_data = {
        "target_idx": 1,
        "messages": [
            {"idx": 0, "author": "홍길동", "timestamp_raw": "오전 10:00", "text": "이전 글", "href": "https://slack.com/p111", "has_thread": False, "reply_count": 0, "item_key": ""},
            {"idx": 1, "author": "김철수", "timestamp_raw": "오전 10:05", "text": "타깃 수임 완료", "href": "https://slack.com/p1786524317769949", "has_thread": False, "reply_count": 0, "item_key": "p1786524317769949"},
            {"idx": 2, "author": "이영희", "timestamp_raw": "오전 10:10", "text": "이후 글", "href": "https://slack.com/p222", "has_thread": False, "reply_count": 0, "item_key": ""},
        ],
    }

    # 3) Capture After (Restored to Channel A)
    snap_after = {
        "url": "https://app.slack.com/client/T123/C_CHANNEL_A",
        "channel_id": "C_CHANNEL_A",
        "conversation_name": "일반",
        "scroll_top": 510,  # 오차 10px
        "scroll_height": 3000,
        "client_height": 800,
        "visible_message_fingerprints": ["fp1", "fp2", "fp3"],
        "first_visible_message": "첫 메시지",
        "last_visible_message": "끝 메시지",
    }

    mock_cdp.evaluate_js = AsyncMock(side_effect=[
        snap_before,  # 1. capture before
        None,         # 2. nav to target
        dom_data,     # 3. extract messages
        None,         # 4. nav back to original url
        {"ok": True}, # 5. scroll top restore
        snap_after,   # 6. capture after in restore_view_state
    ])

    target = SlackSearchResult(
        result_index=0,
        query="수임",
        author="김철수",
        timestamp_raw="오전 10:05",
        text="타깃 수임 완료",
        channel_name="채널B",
        channel_id="C_CHANNEL_B",
        result_url="https://workspace.slack.com/archives/C_CHANNEL_B/p1786524317769949",
        message_fingerprint="fp_target",
    )

    collector = SlackContextCollector(lifecycle_manager=mock_lifecycle_ready, cdp_adapter=mock_cdp)
    with patch("pesu_agent.context.slack_context_collector.is_slack_foreground", return_value=False):
        context: SlackMessageContext = await collector.collect_context(target_result=target)

    assert context.target_verified is True
    assert context.context_collection_succeeded is True
    assert context.state_restore_attempted is True
    assert context.state_restore_succeeded is True
    assert context.overall_status == "SUCCESS"
    assert context.restoration_metrics.url_restored is True
    assert context.restoration_metrics.conversation_restored is True
    assert context.restoration_metrics.scroll_restored is True
    assert context.restoration_metrics.viewport_restored is True
    assert len(context.before_messages) == 1
    assert len(context.after_messages) == 1


@pytest.mark.anyio
async def test_partial_success_context_collected_restore_failed(mock_lifecycle_ready):
    """3. 문맥 수집은 성공했으나 상태 복원 실패 시 상태 분리 검증."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)

    snap_before = {
        "url": "https://app.slack.com/client/T123/C_CHANNEL_A",
        "channel_id": "C_CHANNEL_A",
        "conversation_name": "일반",
        "scroll_top": 500,
        "visible_message_fingerprints": ["fp1"],
    }

    dom_data = {
        "target_idx": 0,
        "messages": [
            {"idx": 0, "author": "김철수", "timestamp_raw": "오전 10:05", "text": "타깃 수임 완료", "href": "https://slack.com/p1786524317769949", "has_thread": False, "reply_count": 0, "item_key": "p1786524317769949"},
        ],
    }

    # 복원 실패: 다른 URL 및 채널에 머무름
    snap_after_failed = {
        "url": "https://app.slack.com/client/T123/C_CHANNEL_B",
        "channel_id": "C_CHANNEL_B",
        "conversation_name": "채널B",
        "scroll_top": 0,
        "visible_message_fingerprints": ["fp_diff"],
    }

    mock_cdp.evaluate_js = AsyncMock(side_effect=[
        snap_before,
        None,
        dom_data,
        None,
        {"ok": False},
        snap_after_failed,
    ])

    target = SlackSearchResult(
        result_index=0,
        query="수임",
        author="김철수",
        timestamp_raw="오전 10:05",
        text="타깃 수임 완료",
        channel_name="채널B",
        channel_id="C_CHANNEL_B",
        result_url="https://workspace.slack.com/archives/C_CHANNEL_B/p1786524317769949",
        message_fingerprint="fp_target",
    )

    collector = SlackContextCollector(lifecycle_manager=mock_lifecycle_ready, cdp_adapter=mock_cdp)
    with patch("pesu_agent.context.slack_context_collector.is_slack_foreground", return_value=False):
        context: SlackMessageContext = await collector.collect_context(target_result=target)

    assert context.context_collection_succeeded is True
    assert context.state_restore_succeeded is False
    assert context.overall_status == "PARTIAL_SUCCESS_CONTEXT_COLLECTED_RESTORE_FAILED"
    assert context.interruption_reason == "restore_failed"


@pytest.mark.anyio
async def test_user_foreground_interference_prevents_start(mock_lifecycle_ready):
    """4. 시작 전 사용자가 Slack 활성화(Foreground) 시 안전 중단 검증."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)

    target = SlackSearchResult(
        result_index=0,
        query="수임",
        author="김철수",
        timestamp_raw="오전 10:05",
        text="타깃",
        channel_name="채널B",
        channel_id="C_CHANNEL_B",
        result_url="https://workspace.slack.com/archives/C_CHANNEL_B/p1786524317769949",
        message_fingerprint="fp_target",
    )

    collector = SlackContextCollector(lifecycle_manager=mock_lifecycle_ready, cdp_adapter=mock_cdp)
    with patch("pesu_agent.context.slack_context_collector.is_slack_foreground", return_value=True):
        context: SlackMessageContext = await collector.collect_context(target_result=target)

    assert context.target_verified is False
    assert context.context_collection_succeeded is False
    assert context.overall_status == "INTERRUPTED_BY_USER"
    assert context.interruption_reason == "user_opened_slack"


@pytest.mark.anyio
async def test_mid_operation_user_foreground_interruption_and_restore(mock_lifecycle_ready):
    """4-1. 조사 도중(Renderer 변경 후) 사용자가 Slack 활성화 시 중단 및 Channel A 복원 검증 (Technical Debt B)."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)

    snap_before = {
        "url": "https://app.slack.com/client/T123/C_CHANNEL_A",
        "channel_id": "C_CHANNEL_A",
        "conversation_name": "일반",
        "scroll_top": 500,
        "visible_message_fingerprints": ["fp1"],
    }

    dom_data = {
        "target_idx": 0,
        "messages": [
            {"idx": 0, "author": "김철수", "timestamp_raw": "오전 10:05", "text": "타깃", "href": "https://slack.com/p1786524317769949", "has_thread": False, "reply_count": 0, "item_key": "p1786524317769949"},
        ],
    }

    snap_after_restored = {
        "url": "https://app.slack.com/client/T123/C_CHANNEL_A",
        "channel_id": "C_CHANNEL_A",
        "conversation_name": "일반",
        "scroll_top": 500,
        "visible_message_fingerprints": ["fp1"],
    }

    mock_cdp.evaluate_js = AsyncMock(side_effect=[
        snap_before,          # 1. capture before state
        None,                 # 2. nav to target channel B
        dom_data,             # 3. dom extract
        None,                 # 4. restore nav back to channel A
        {"ok": True},         # 5. restore scroll
        snap_after_restored,  # 6. capture after state in restore
    ])

    target = SlackSearchResult(
        result_index=0,
        query="수임",
        author="김철수",
        timestamp_raw="오전 10:05",
        text="타깃",
        channel_name="채널B",
        channel_id="C_CHANNEL_B",
        result_url="https://workspace.slack.com/archives/C_CHANNEL_B/p1786524317769949",
        message_fingerprint="fp_target",
    )

    collector = SlackContextCollector(lifecycle_manager=mock_lifecycle_ready, cdp_adapter=mock_cdp)

    # is_slack_foreground side_effect:
    # 1st call (before start) -> False
    # 2nd call (before nav) -> False
    # 3rd call (mid-operation after dom extract) -> True!
    # 4th call (during restore) -> False
    fg_calls = [False, False, True, False, False]
    with patch("pesu_agent.context.slack_context_collector.is_slack_foreground", side_effect=fg_calls):
        context: SlackMessageContext = await collector.collect_context(target_result=target)

    assert context.overall_status == "INTERRUPTED_BY_USER"
    assert context.interruption_reason == "user_opened_slack"
    assert context.context_collection_succeeded is False
    assert context.state_restore_attempted is True
    assert context.state_restore_succeeded is True
    assert context.restoration_metrics.url_restored is True
    assert context.restoration_metrics.conversation_restored is True


@pytest.mark.anyio
async def test_thread_positive_extraction(mock_lifecycle_ready):
    """5. 실제 스레드 댓글(Thread Positive: 2개의 댓글) 식별 및 추출 검증."""
    mock_cdp = MagicMock(spec=SlackCdpAdapter)

    snap_state = {"url": "https://app.slack.com/client/T123/C1", "channel_id": "C1", "scroll_top": 0}
    dom_data = {
        "target_idx": 0,
        "messages": [
            {
                "idx": 0,
                "author": "한경민",
                "timestamp_raw": "오후 5:00",
                "text": "검토 부탁드립니다.",
                "href": "https://slack.com/p1786608054914119",
                "has_thread": True,
                "reply_count": 2,
                "item_key": "p1786608054914119",
            }
        ],
    }

    mock_cdp.evaluate_js = AsyncMock(side_effect=[
        snap_state,
        None,
        dom_data,
        None,
        snap_state,
        snap_state,
    ])

    target = SlackSearchResult(
        result_index=0,
        query="검토",
        author="한경민",
        timestamp_raw="오후 5:00",
        text="검토 부탁드립니다.",
        channel_name="본점-기장영업",
        channel_id="C094CNHQFMX",
        result_url="https://workspace.slack.com/archives/C094CNHQFMX/p1786608054914119",
        message_fingerprint="fp1",
    )

    collector = SlackContextCollector(lifecycle_manager=mock_lifecycle_ready, cdp_adapter=mock_cdp)
    with patch("pesu_agent.context.slack_context_collector.is_slack_foreground", return_value=False):
        context: SlackMessageContext = await collector.collect_context(target_result=target)

    assert context.target_verified is True
    assert context.has_thread is True
    assert context.reply_count == 2
    assert context.thread_identifier_candidate == "1786608054914119"
    assert context.target_message.reply_count == 2
