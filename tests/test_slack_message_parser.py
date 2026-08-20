"""Unit tests for SlackMessageParser, Author Resolution, and Scroll-Safe Message Fingerprints.

All test fixtures use generic synthetic data without any real personal or company information.
"""

import json
from pathlib import Path

import pytest

from pesu_agent.adapters.slack_desktop import SlackDesktopAdapter, SlackElementNode, SlackNotFoundError
from pesu_agent.parsers.slack_message_parser import (
    SlackMessage,
    SlackMessageParser,
    SlackVisibleMessagesResult,
)


def make_node(
    name: str = "",
    control_type: str = "Pane",
    automation_id: str = "",
    class_name: str = "",
    depth: int = 0,
    children: list[SlackElementNode] | None = None,
) -> SlackElementNode:
    """Helper to build synthetic SlackElementNode hierarchies."""
    return SlackElementNode(
        depth=depth,
        name=name,
        control_type=control_type,
        automation_id=automation_id,
        class_name=class_name,
        is_enabled=True,
        is_visible=True,
        rectangle=None,
        process_id=1234,
        is_truncated=False,
        truncation_reason=None,
        children=children or [],
    )


def test_slack_message_model_fields():
    msg = SlackMessage(
        viewport_index=0,
        author="User1",
        author_raw="User1",
        author_resolved="User1",
        author_resolution="explicit",
        timestamp_raw="어제, 오후 3:29:01",
        text="테스트 메시지 본문입니다.",
        mentions=["@User2"],
        links=["https://example.com/item/1"],
        context="channel",
        source_container="List:message-list:10",
        source_node_name="ListItem Source",
        tree_depth=15,
        message_fingerprint="abcdef123456",
    )
    assert msg.viewport_index == 0
    assert msg.author == "User1"
    assert msg.author_raw == "User1"
    assert msg.author_resolved == "User1"
    assert msg.author_resolution == "explicit"
    assert msg.timestamp_raw == "어제, 오후 3:29:01"
    assert msg.mentions == ["@User2"]
    assert msg.links == ["https://example.com/item/1"]
    assert msg.context == "channel"
    assert msg.source_container == "List:message-list:10"
    assert msg.message_fingerprint == "abcdef123456"

    data = msg.model_dump()
    assert data["viewport_index"] == 0
    assert data["author_resolved"] == "User1"
    json_str = json.dumps(data, ensure_ascii=False)
    assert "어제, 오후 3:29:01" in json_str


def test_viewport_index_assignment():
    """Test that viewport_index is sequentially assigned (0, 1, 2) in traversal order."""
    item1 = make_node(
        name="UserA 오후 1:00 메시지1",
        control_type="ListItem",
        automation_id="message-list_100.1",
        depth=15,
        children=[
            make_node(name="UserA", control_type="Button", class_name="c-message__sender_button", depth=16),
            make_node(name="메시지1 내용", control_type="Text", depth=16),
        ],
    )
    item2 = make_node(
        name="UserA 오후 1:01 메시지2",
        control_type="ListItem",
        automation_id="message-list_100.2",
        depth=15,
        children=[
            make_node(name="메시지2 내용", control_type="Text", depth=16),
        ],
    )
    item3 = make_node(
        name="UserB 오후 1:02 메시지3",
        control_type="ListItem",
        automation_id="message-list_100.3",
        depth=15,
        children=[
            make_node(name="UserB", control_type="Button", class_name="c-message__sender_button", depth=16),
            make_node(name="메시지3 내용", control_type="Text", depth=16),
        ],
    )

    root = make_node(name="General - Slack", control_type="Window", children=[item1, item2, item3])
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    assert result.message_count == 3
    assert result.messages[0].viewport_index == 0
    assert result.messages[1].viewport_index == 1
    assert result.messages[2].viewport_index == 2


def test_scroll_safe_fingerprint_independence_from_author_resolution():
    """Test that message_fingerprint is 100% stable regardless of author_resolved (viewport shift)."""
    # Scenario A: Message is 2nd in viewport -> author_resolved becomes "UserA" (inherited)
    item_prev = make_node(
        name="UserA 10:00",
        control_type="ListItem",
        automation_id="message-list_100.0",
        depth=15,
        children=[
            make_node(name="UserA", control_type="Button", class_name="c-message__sender_button", depth=16),
            make_node(name="이전 메시지", control_type="Text", depth=16),
        ],
    )
    target_item_with_prev = make_node(
        name="10:01",
        control_type="ListItem",
        automation_id="message-list_100.1",
        depth=15,
        children=[
            make_node(name="오전 10:01", control_type="Hyperlink", class_name="c-timestamp", depth=16),
            make_node(name="동일한 본문 텍스트입니다.", control_type="Text", depth=16),
        ],
    )
    root_with_prev = make_node(name="Slack", control_type="Window", children=[item_prev, target_item_with_prev])

    parser = SlackMessageParser()
    res_with_prev = parser.parse_from_tree(root_with_prev)
    msg_with_prev = res_with_prev.messages[1]
    assert msg_with_prev.author_resolved == "UserA"
    assert msg_with_prev.author_resolution == "inherited_from_previous_message"
    fp_inherited = msg_with_prev.message_fingerprint

    # Scenario B: User scrolled down so previous message is offscreen -> author_resolved becomes None (unresolved)
    target_item_at_top = make_node(
        name="10:01",
        control_type="ListItem",
        automation_id="message-list_100.1",
        depth=15,
        children=[
            make_node(name="오전 10:01", control_type="Hyperlink", class_name="c-timestamp", depth=16),
            make_node(name="동일한 본문 텍스트입니다.", control_type="Text", depth=16),
        ],
    )
    root_at_top = make_node(name="Slack", control_type="Window", children=[target_item_at_top])

    res_at_top = parser.parse_from_tree(root_at_top)
    msg_at_top = res_at_top.messages[0]
    assert msg_at_top.author_resolved is None
    assert msg_at_top.author_resolution == "unresolved"
    fp_unresolved = msg_at_top.message_fingerprint

    # The fingerprint MUST be identical across both scroll viewports!
    assert fp_inherited == fp_unresolved
    assert len(fp_inherited) == 64


def test_nested_bullet_list_items_not_separated_into_extra_messages():
    """Test that rich text bullet ListItems inside a message are combined into parent message text."""
    bullet1 = make_node(name="• 첫 번째 항목", control_type="ListItem", depth=18)
    bullet2 = make_node(name="• 두 번째 항목", control_type="ListItem", depth=18)
    nested_list = make_node(
        control_type="List",
        class_name="p-rich_text_list",
        depth=17,
        children=[bullet1, bullet2],
    )

    parent_msg_item = make_node(
        name="UserC 글머리 기호 메시지",
        control_type="ListItem",
        automation_id="message-list_200.1",
        depth=15,
        children=[
            make_node(name="UserC", control_type="Button", class_name="c-message__sender_button", depth=16),
            make_node(name="오후 3:00", control_type="Hyperlink", class_name="c-timestamp", depth=16),
            make_node(name="아래 목록을 확인하세요:", control_type="Text", depth=16),
            nested_list,
            make_node(name="이상입니다.", control_type="Text", depth=16),
        ],
    )

    root = make_node(name="Slack", control_type="Window", children=[parent_msg_item])
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    # Must produce exactly 1 message, NOT 3 messages!
    assert result.message_count == 1
    assert result.unique_fingerprints_count == 1
    assert result.duplicate_fingerprint_groups_count == 0

    msg = result.messages[0]
    assert msg.author_resolved == "UserC"
    assert "아래 목록을 확인하세요:" in msg.text
    assert "• 첫 번째 항목" in msg.text
    assert "• 두 번째 항목" in msg.text
    assert "이상입니다." in msg.text


def test_author_resolution_explicit_and_inherited():
    """Test that consecutive messages in the same container inherit author from previous message."""
    item1 = make_node(
        name="UserA 오후 3:00 첫 번째 메시지",
        control_type="ListItem",
        automation_id="message-list_300.1",
        depth=15,
        children=[
            make_node(name="UserA", control_type="Button", class_name="c-message__sender_button", depth=16),
            make_node(name="오늘, 오후 3:00:00", control_type="Hyperlink", class_name="c-timestamp", depth=16),
            make_node(name="첫 번째 메시지입니다.", control_type="Text", depth=16),
        ],
    )
    item2 = make_node(
        name="오후 3:01 두 번째 연속 메시지",
        control_type="ListItem",
        automation_id="message-list_300.2",
        depth=15,
        children=[
            make_node(name="오늘, 오후 3:01:00", control_type="Hyperlink", class_name="c-timestamp", depth=16),
            make_node(name="두 번째 연속 메시지입니다.", control_type="Text", depth=16),
        ],
    )

    root = make_node(name="General - Slack", control_type="Window", children=[item1, item2])
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    assert result.message_count == 2
    assert result.explicit_author_count == 1
    assert result.inherited_author_count == 1
    assert result.unresolved_author_count == 0

    m1 = result.messages[0]
    assert m1.author_resolved == "UserA"
    assert m1.author_resolution == "explicit"

    m2 = result.messages[1]
    assert m2.author_resolved == "UserA"
    assert m2.author_resolution == "inherited_from_previous_message"


def test_first_message_author_unresolved():
    """Test that if the first visible message lacks author_raw, it stays unresolved (no offscreen guessing)."""
    item1 = make_node(
        name="오후 1:00 첫 번째 메시지 (작성자 생략)",
        control_type="ListItem",
        automation_id="message-list_400.1",
        depth=15,
        children=[
            make_node(name="첫 화면 상단에 렌더링된 메시지입니다.", control_type="Text", depth=16),
        ],
    )

    root = make_node(name="General - Slack", control_type="Window", children=[item1])
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    assert result.message_count == 1
    assert result.unresolved_author_count == 1

    msg = result.messages[0]
    assert msg.author_resolved is None
    assert msg.author_resolution == "unresolved"


def test_author_inheritance_isolated_between_containers():
    """Test that author is NOT inherited across different list containers (e.g. channel vs thread)."""
    item_ch1 = make_node(
        name="UserAlice 메시지",
        control_type="ListItem",
        automation_id="message-list_ch_1",
        depth=15,
        children=[
            make_node(name="UserAlice", control_type="Button", class_name="c-message__sender_button", depth=16),
            make_node(name="채널 메시지입니다.", control_type="Text", depth=16),
        ],
    )
    ch_container = make_node(
        name="Channel View",
        control_type="List",
        automation_id="channel_list_container",
        depth=14,
        children=[item_ch1],
    )

    item_th1 = make_node(
        name="스레드 첫 메시지 (작성자 없음)",
        control_type="ListItem",
        automation_id="message-list_th_1",
        depth=15,
        children=[
            make_node(name="스레드 패널의 메시지입니다.", control_type="Text", depth=16),
        ],
    )
    th_container = make_node(
        name="Thread View",
        control_type="List",
        automation_id="thread_list_container",
        depth=14,
        children=[item_th1],
    )

    root = make_node(name="Slack", control_type="Window", children=[ch_container, th_container])
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    assert result.message_count == 2
    assert result.messages[0].author_resolved == "UserAlice"
    assert result.messages[0].author_resolution == "explicit"

    # Thread message must NOT inherit UserAlice from the channel container!
    assert result.messages[1].author_resolved is None
    assert result.messages[1].author_resolution == "unresolved"


def test_fingerprint_deterministic_and_unique():
    """Test SHA-256 fingerprint generation stability and collision resistance."""
    fp1 = SlackMessageParser.compute_fingerprint(
        context="channel",
        timestamp_raw="오늘, 오후 12:00:00",
        text="테스트 본문 내용",
        mentions=["@UserB", "@UserC"],
        links=["https://example.com/a"],
        uia_message_id="12345.67",
    )
    fp2 = SlackMessageParser.compute_fingerprint(
        context="channel",
        timestamp_raw="오늘, 오후 12:00:00",
        text="테스트 본문 내용",
        mentions=["@UserC", "@UserB"],
        links=["https://example.com/a"],
        uia_message_id="12345.67",
    )
    assert fp1 == fp2
    assert len(fp1) == 64

    fp_diff_text = SlackMessageParser.compute_fingerprint(
        context="channel",
        timestamp_raw="오늘, 오후 12:00:00",
        text="다른 본문 내용",
        mentions=["@UserB", "@UserC"],
        links=["https://example.com/a"],
        uia_message_id="12345.67",
    )
    assert fp1 != fp_diff_text


def test_save_and_load_visible_messages_json(tmp_path: Path):
    parser = SlackMessageParser()
    sample_result = SlackVisibleMessagesResult(
        captured_at="2026-08-20T04:25:00Z",
        slack_window_title="Test - Slack",
        message_count=1,
        scope="visible_uia_only",
        is_complete_conversation=False,
        excluded_candidates_count=3,
        explicit_author_count=1,
        inherited_author_count=0,
        unresolved_author_count=0,
        unique_fingerprints_count=1,
        duplicate_fingerprint_groups_count=0,
        messages=[
            SlackMessage(
                viewport_index=0,
                author="UserTest",
                author_raw="UserTest",
                author_resolved="UserTest",
                author_resolution="explicit",
                timestamp_raw="오후 1:00",
                text="한글 테스트 메시지",
                mentions=["@UserY"],
                links=[],
                context="channel",
                source_container="List:main:12",
                source_node_name="Source Test",
                tree_depth=15,
                message_fingerprint="1234567890abcdef",
            )
        ],
    )

    out_file = tmp_path / "visible_messages.json"
    saved = parser.save_json(sample_result, out_file)
    assert saved.exists()

    content = saved.read_text(encoding="utf-8")
    assert "한글 테스트 메시지" in content
    assert '"viewport_index": 0' in content
    assert '"author_resolution": "explicit"' in content
    assert '"unique_fingerprints_count": 1' in content


@pytest.mark.live_slack
def test_live_slack_message_parsing():
    """Live smoke test on real Slack if available."""
    adapter = SlackDesktopAdapter()
    try:
        slack_win = adapter.find_slack_window()
    except SlackNotFoundError:
        pytest.skip("Slack이 실행 중이지 않아 Live Slack 파싱 테스트를 건너뜁니다.")

    tree_res = adapter.inspect_tree(slack_win, max_depth=25, max_elements=3000)
    parser = SlackMessageParser()
    parsed_res = parser.parse_from_tree(tree_res)

    assert isinstance(parsed_res, SlackVisibleMessagesResult)
    assert parsed_res.scope == "visible_uia_only"
    assert parsed_res.is_complete_conversation is False
    assert parsed_res.unique_fingerprints_count > 0
