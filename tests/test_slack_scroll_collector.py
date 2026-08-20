"""Unit tests for SlackScrollCollector and Overlap Stitching (MVP 2).

All tests use synthetic, mock tree hierarchies without relying on real Slack or live windows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from pesu_agent.adapters.slack_desktop import InspectionResult, SlackDesktopAdapter, SlackElementNode
from pesu_agent.collectors.slack_scroll_collector import (
    SlackConversationCollection,
    SlackScrollCollector,
)
from pesu_agent.parsers.slack_message_parser import SlackMessage, SlackMessageParser


def make_msg_node(
    aid: str,
    author: str,
    time_str: str,
    text: str,
    depth: int = 15,
) -> SlackElementNode:
    """Builds a synthetic ListItem message element."""
    return SlackElementNode(
        depth=depth,
        name=f"{author} {time_str} {text}",
        control_type="ListItem",
        automation_id=f"message-list_{aid}",
        class_name="c-virtual_list__item",
        is_enabled=True,
        is_visible=True,
        rectangle=None,
        process_id=1234,
        is_truncated=False,
        truncation_reason=None,
        children=[
            SlackElementNode(
                depth=depth + 1,
                name=author,
                control_type="Button",
                class_name="c-message__sender_button",
                is_enabled=True,
                is_visible=True,
                rectangle=None,
                process_id=1234,
                is_truncated=False,
                truncation_reason=None,
                children=[],
            ),
            SlackElementNode(
                depth=depth + 1,
                name=time_str,
                control_type="Hyperlink",
                class_name="c-timestamp",
                is_enabled=True,
                is_visible=True,
                rectangle=None,
                process_id=1234,
                is_truncated=False,
                truncation_reason=None,
                children=[],
            ),
            SlackElementNode(
                depth=depth + 1,
                name=text,
                control_type="Text",
                class_name="",
                is_enabled=True,
                is_visible=True,
                rectangle=None,
                process_id=1234,
                is_truncated=False,
                truncation_reason=None,
                children=[],
            ),
        ],
    )


def make_mock_tree(window_title: str, msg_nodes: list[SlackElementNode]) -> InspectionResult:
    """Builds a mock InspectionResult with given message nodes."""
    root = SlackElementNode(
        depth=0,
        name=window_title,
        control_type="Window",
        automation_id="",
        class_name="Chrome_WidgetWin_1",
        is_enabled=True,
        is_visible=True,
        rectangle=None,
        process_id=1234,
        is_truncated=False,
        truncation_reason=None,
        children=[
            SlackElementNode(
                depth=14,
                name="Message List",
                control_type="List",
                automation_id="c-virtual_list__scroll_container",
                class_name="c-virtual_list",
                is_enabled=True,
                is_visible=True,
                rectangle=None,
                process_id=1234,
                is_truncated=False,
                truncation_reason=None,
                children=msg_nodes,
            )
        ],
    )
    return InspectionResult(
        slack_window_title=window_title,
        slack_process_id=1234,
        timestamp="2026-08-20T06:00:00Z",
        duration_seconds=0.1,
        total_elements=len(msg_nodes) + 2,
        max_depth_reached=16,
        is_truncated=False,
        truncation_reason=None,
        control_type_counts={"Window": 1, "List": 1, "ListItem": len(msg_nodes)},
        root=root,
    )


def test_overlap_deduplication_and_stitching():
    """Test 1 & 2 & 3: Viewport overlap is deduplicated and older messages are prepended chronologically."""
    # Viewport 0: [Msg3, Msg4, Msg5]
    vp0_nodes = [
        make_msg_node("103", "Alice", "오전 10:03", "메시지 3"),
        make_msg_node("104", "Bob", "오전 10:04", "메시지 4"),
        make_msg_node("105", "Alice", "오전 10:05", "메시지 5"),
    ]
    # Viewport 1 (after 1 scroll up): [Msg1, Msg2, Msg3, Msg4] (Msg3, Msg4 overlap, Msg1, Msg2 are new older msgs)
    vp1_nodes = [
        make_msg_node("101", "Alice", "오전 10:01", "메시지 1"),
        make_msg_node("102", "Bob", "오전 10:02", "메시지 2"),
        make_msg_node("103", "Alice", "오전 10:03", "메시지 3"),
        make_msg_node("104", "Bob", "오전 10:04", "메시지 4"),
    ]

    trees = [
        make_mock_tree("홍길동(DM) - Slack", vp0_nodes),
        make_mock_tree("홍길동(DM) - Slack", vp1_nodes),
        make_mock_tree("홍길동(DM) - Slack", vp1_nodes),  # Viewport 2: same (reached top)
    ]

    mock_adapter = MagicMock()
    mock_adapter.inspect_tree.side_effect = trees

    collector = SlackScrollCollector(
        adapter=mock_adapter,
        scroll_executor=lambda win, c: (True, "mock_scroll"),
        sleep_func=lambda s: None,
    )

    collection = collector.collect_conversation(
        slack_window=MagicMock(),
        max_scrolls=5,
        no_new_message_limit=1,
    )

    # Must contain exactly 5 unique messages in chronological order [Msg1, Msg2, Msg3, Msg4, Msg5]
    assert collection.message_count == 5
    assert collection.unique_message_count == 5
    assert [m.text for m in collection.messages] == [
        "메시지 1",
        "메시지 2",
        "메시지 3",
        "메시지 4",
        "메시지 5",
    ]
    assert collection.first_visible_message.text == "메시지 1"
    assert collection.last_visible_message.text == "메시지 5"
    assert collection.context == "dm"
    assert collection.conversation_name == "홍길동(DM)"


def test_stop_on_max_scrolls():
    """Test 5: Terminates with stop_reason='max_scrolls' when max_scrolls is reached."""
    # Always return new messages
    def tree_generator():
        idx = 100
        while True:
            nodes = [make_msg_node(str(idx - i), "User", "오후 1:00", f"메시지 {idx - i}") for i in range(3)]
            idx -= 3
            yield make_mock_tree("일반(채널) - Slack", nodes)

    gen = tree_generator()
    mock_adapter = MagicMock()
    mock_adapter.inspect_tree.side_effect = lambda win, **kw: next(gen)

    collector = SlackScrollCollector(
        adapter=mock_adapter,
        scroll_executor=lambda win, c: (True, "mock_scroll"),
        sleep_func=lambda s: None,
    )

    collection = collector.collect_conversation(
        slack_window=MagicMock(),
        max_scrolls=3,
        max_messages=100,
        no_new_message_limit=5,
    )

    assert collection.stop_reason == "max_scrolls"
    assert collection.scroll_iterations == 3


def test_stop_on_max_messages():
    """Test 6: Terminates with stop_reason='max_messages' when max_messages is exceeded."""
    def tree_generator():
        idx = 500
        while True:
            nodes = [make_msg_node(str(idx - i), "User", "오후 1:00", f"메시지 {idx - i}") for i in range(10)]
            idx -= 10
            yield make_mock_tree("일반(채널) - Slack", nodes)

    gen = tree_generator()
    mock_adapter = MagicMock()
    mock_adapter.inspect_tree.side_effect = lambda win, **kw: next(gen)

    collector = SlackScrollCollector(
        adapter=mock_adapter,
        scroll_executor=lambda win, c: (True, "mock_scroll"),
        sleep_func=lambda s: None,
    )

    collection = collector.collect_conversation(
        slack_window=MagicMock(),
        max_scrolls=10,
        max_messages=15,
        no_new_message_limit=5,
    )

    assert collection.stop_reason == "max_messages"
    assert collection.message_count >= 15


def test_stop_on_container_lost_or_conversation_change():
    """Test 7 & 8: Stops immediately if conversation_key or context changes mid-scroll."""
    vp0 = [make_msg_node("1", "User1", "오후 1:00", "채널 대화")]
    vp1_different_conv = [make_msg_node("2", "User2", "오후 1:00", "다른 채널 대화")]

    trees = [
        make_mock_tree("channel-a(채널) - Slack", vp0),
        make_mock_tree("channel-b(채널) - Slack", vp1_different_conv),
    ]

    mock_adapter = MagicMock()
    mock_adapter.inspect_tree.side_effect = trees

    collector = SlackScrollCollector(
        adapter=mock_adapter,
        scroll_executor=lambda win, c: (True, "mock_scroll"),
        sleep_func=lambda s: None,
    )

    collection = collector.collect_conversation(
        slack_window=MagicMock(),
        max_scrolls=5,
    )

    assert collection.stop_reason == "container_lost"


def test_stop_on_no_new_messages_and_reached_start():
    """Test 4 & 9: Stops when viewport remains unchanged for consecutive scrolls."""
    vp = [make_msg_node("1", "User1", "오후 1:00", "첫 번째 메시지")]
    trees = [
        make_mock_tree("일반(채널) - Slack", vp),
        make_mock_tree("일반(채널) - Slack", vp),
        make_mock_tree("일반(채널) - Slack", vp),
        make_mock_tree("일반(채널) - Slack", vp),
    ]

    mock_adapter = MagicMock()
    mock_adapter.inspect_tree.side_effect = trees

    collector = SlackScrollCollector(
        adapter=mock_adapter,
        scroll_executor=lambda win, c: (True, "mock_scroll"),
        sleep_func=lambda s: None,
    )

    collection = collector.collect_conversation(
        slack_window=MagicMock(),
        max_scrolls=10,
        no_new_message_limit=2,
    )

    assert collection.stop_reason in ("reached_start", "no_new_messages")
    assert collection.is_reached_start is True


def test_stop_on_scroll_failure():
    """Test 10: Gracefully handles scroll executor failure."""
    vp = [make_msg_node("1", "User1", "오후 1:00", "단일 메시지")]
    mock_adapter = MagicMock()
    mock_adapter.inspect_tree.return_value = make_mock_tree("일반(채널) - Slack", vp)

    collector = SlackScrollCollector(
        adapter=mock_adapter,
        scroll_executor=lambda win, c: (False, "none"),
        sleep_func=lambda s: None,
    )

    collection = collector.collect_conversation(
        slack_window=MagicMock(),
        max_scrolls=5,
    )

    assert collection.stop_reason == "scroll_not_possible"
    assert collection.message_count == 1


def test_save_json_collection(tmp_path: Path):
    """Test saving SlackConversationCollection to UTF-8 JSON file."""
    col = SlackConversationCollection(
        captured_at="2026-08-20T06:00:00Z",
        conversation_name="김학진(DM)",
        conversation_key="dm:김학진(DM)",
        context="dm",
        scroll_direction="up",
        scroll_iterations=2,
        scroll_method="uia_mouse_wheel",
        message_count=1,
        unique_message_count=1,
        stop_reason="max_scrolls",
        is_reached_start=False,
        is_complete=False,
        messages=[
            SlackMessage(
                viewport_index=0,
                author="김학진",
                author_raw="김학진",
                author_resolved="김학진",
                author_resolution="explicit",
                timestamp_raw="오후 1:00",
                text="테스트 메시지",
                context="dm",
                message_fingerprint="abc123hash",
            )
        ],
    )

    out_file = tmp_path / "slack_conversation.json"
    saved = SlackScrollCollector.save_json(col, out_file)
    assert saved.exists()

    content = saved.read_text(encoding="utf-8")
    assert "김학진(DM)" in content
    assert "uia_mouse_wheel" in content
    assert "테스트 메시지" in content
