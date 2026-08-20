"""Unit tests for SlackMessageParser and SlackMessage data structures.

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


def test_slack_message_model():
    msg = SlackMessage(
        author="User1",
        timestamp_raw="어제, 오후 3:29:01",
        text="테스트 메시지 본문입니다.",
        mentions=["@User2"],
        links=["https://example.com/item/1"],
        context="channel",
        source_node_name="ListItem Source",
        tree_depth=15,
    )
    assert msg.author == "User1"
    assert msg.timestamp_raw == "어제, 오후 3:29:01"
    assert msg.mentions == ["@User2"]
    assert msg.links == ["https://example.com/item/1"]
    assert msg.context == "channel"

    data = msg.model_dump()
    assert data["author"] == "User1"
    json_str = json.dumps(data, ensure_ascii=False)
    assert "어제, 오후 3:29:01" in json_str


def test_parse_author_time_text_message():
    """Test parsing a standard message with author, timestamp, and text."""
    item = make_node(
        name="UserA 오후 3:00 안녕하세요 반갑습니다",
        control_type="ListItem",
        automation_id="message-list_123456789.001",
        class_name="c-virtual_list__item",
        depth=15,
        children=[
            make_node(control_type="Document", class_name="c-message_kit__hover", depth=16, children=[
                make_node(name="UserA", control_type="Button", class_name="c-link--button c-message__sender_button", depth=17),
                make_node(name="오늘, 오후 3:00:15", control_type="Hyperlink", class_name="c-link c-timestamp", depth=17),
                make_node(name="안녕하세요 반갑습니다", control_type="Text", depth=17),
            ])
        ],
    )

    root = make_node(name="general - Workspace - Slack", control_type="Window", children=[item])
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    assert result.message_count == 1
    assert result.excluded_candidates_count == 0
    msg = result.messages[0]
    assert msg.author == "UserA"
    assert msg.timestamp_raw == "오늘, 오후 3:00:15"
    assert msg.text == "안녕하세요 반갑습니다"
    assert msg.mentions == []
    assert msg.links == []
    assert msg.context == "channel"


def test_parse_message_with_mentions_and_links():
    """Test parsing a message containing user mentions and URLs."""
    item = make_node(
        name="UserB 오후 4:10 @UserC 업무 링크 https://example.com/doc",
        control_type="ListItem",
        automation_id="message-list_123456789.002",
        class_name="c-virtual_list__item",
        depth=15,
        children=[
            make_node(control_type="Document", depth=16, children=[
                make_node(name="UserB", control_type="Button", class_name="c-message__sender_button", depth=17),
                make_node(name="어제, 오후 4:10:00. 채널에서 열기", control_type="Hyperlink", class_name="c-link c-timestamp", depth=17),
                make_node(name="@UserC", control_type="Hyperlink", class_name="c-member_slug", depth=17),
                make_node(name="업무 링크", control_type="Text", depth=17),
                make_node(name="https://example.com/doc", control_type="Hyperlink", class_name="c-link--underline", depth=17),
            ])
        ],
    )

    root = make_node(name="dev-chat - Slack", control_type="Window", children=[item])
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    assert result.message_count == 1
    msg = result.messages[0]
    assert msg.author == "UserB"
    assert msg.timestamp_raw == "어제, 오후 4:10:00. 채널에서 열기"
    assert msg.mentions == ["@UserC"]
    assert msg.links == ["https://example.com/doc"]
    assert "@UserC 업무 링크 https://example.com/doc" in msg.text


def test_parse_split_text_nodes():
    """Test combining multiple child Text nodes in document order."""
    item = make_node(
        name="UserD 메시지 여러 조각",
        control_type="ListItem",
        automation_id="message-list_123456789.003",
        depth=15,
        children=[
            make_node(name="UserD", control_type="Button", class_name="c-message__sender_button", depth=16),
            make_node(name="오후 5:20", control_type="Hyperlink", class_name="c-timestamp", depth=16),
            make_node(name="첫 번째 줄 내용.", control_type="Text", depth=16),
            make_node(name="", control_type="Text", depth=16),  # Empty text node
            make_node(name="두 번째 줄 상세 설명입니다.", control_type="Text", depth=16),
        ],
    )

    root = make_node(name="Team - Slack", control_type="Window", children=[item])
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    assert result.message_count == 1
    msg = result.messages[0]
    assert msg.text == "첫 번째 줄 내용. 두 번째 줄 상세 설명입니다."


def test_parse_missing_author_and_time():
    """Test consecutive messages where author or timestamp is omitted."""
    item = make_node(
        name="연속 메시지 본문",
        control_type="ListItem",
        automation_id="message-list_123456789.004",
        depth=15,
        children=[
            make_node(name="작성자 표시 없는 추가 메시지 내용입니다.", control_type="Text", depth=16),
        ],
    )

    root = make_node(name="Team - Slack", control_type="Window", children=[item])
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    assert result.message_count == 1
    msg = result.messages[0]
    assert msg.author is None
    assert msg.timestamp_raw is None
    assert msg.text == "작성자 표시 없는 추가 메시지 내용입니다."


def test_different_list_items_isolation():
    """Test that two adjacent ListItems maintain strict subtree isolation."""
    item1 = make_node(
        name="Item1",
        control_type="ListItem",
        automation_id="message-list_001",
        depth=15,
        children=[
            make_node(name="Alice", control_type="Button", class_name="c-message__sender_button", depth=16),
            make_node(name="오전 10:00", control_type="Hyperlink", class_name="c-timestamp", depth=16),
            make_node(name="Alice의 메시지", control_type="Text", depth=16),
        ],
    )
    item2 = make_node(
        name="Item2",
        control_type="ListItem",
        automation_id="message-list_002",
        depth=15,
        children=[
            make_node(name="Bob", control_type="Button", class_name="c-message__sender_button", depth=16),
            make_node(name="오전 10:01", control_type="Hyperlink", class_name="c-timestamp", depth=16),
            make_node(name="Bob의 메시지", control_type="Text", depth=16),
        ],
    )

    root = make_node(name="General - Slack", control_type="Window", children=[item1, item2])
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    assert result.message_count == 2
    assert result.messages[0].author == "Alice"
    assert result.messages[0].text == "Alice의 메시지"
    assert result.messages[1].author == "Bob"
    assert result.messages[1].text == "Bob의 메시지"


def test_exclude_non_message_ui_elements():
    """Test excluding dividers, spacers, and non-message list items."""
    spacer = make_node(
        name="",
        control_type="ListItem",
        automation_id="message-list_bottomSpacer",
        depth=15,
    )
    unread_divider = make_node(
        name="새 항목",
        control_type="ListItem",
        automation_id="message-list_unreadDivider",
        depth=15,
    )
    valid_msg = make_node(
        name="유효 메시지",
        control_type="ListItem",
        automation_id="message-list_12345",
        depth=15,
        children=[
            make_node(name="UserX", control_type="Button", class_name="c-message__sender_button", depth=16),
            make_node(name="정상 텍스트", control_type="Text", depth=16),
        ],
    )

    root = make_node(
        name="Slack",
        control_type="Window",
        children=[spacer, unread_divider, valid_msg],
    )
    parser = SlackMessageParser()
    result = parser.parse_from_tree(root)

    assert result.message_count == 1
    assert result.excluded_candidates_count == 2
    assert result.messages[0].text == "정상 텍스트"


def test_save_and_load_visible_messages_json(tmp_path: Path):
    parser = SlackMessageParser()
    sample_result = SlackVisibleMessagesResult(
        captured_at="2026-08-20T04:25:00Z",
        slack_window_title="Test - Slack",
        message_count=1,
        scope="visible_uia_only",
        is_complete_conversation=False,
        excluded_candidates_count=3,
        messages=[
            SlackMessage(
                author="UserTest",
                timestamp_raw="오후 1:00",
                text="한글 테스트 메시지",
                mentions=["@UserY"],
                links=[],
                context="channel",
                source_node_name="Source Test",
                tree_depth=15,
            )
        ],
    )

    out_file = tmp_path / "visible_messages.json"
    saved = parser.save_json(sample_result, out_file)
    assert saved.exists()

    content = saved.read_text(encoding="utf-8")
    assert "한글 테스트 메시지" in content
    assert '"scope": "visible_uia_only"' in content
    assert '"is_complete_conversation": false' in content
    assert '"excluded_candidates_count": 3' in content


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
