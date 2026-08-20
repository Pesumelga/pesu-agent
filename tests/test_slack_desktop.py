"""Unit and integration tests for SlackDesktopAdapter and UIA data structures."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pesu_agent.adapters.slack_desktop import (
    InspectionResult,
    RectangleModel,
    SlackDesktopAdapter,
    SlackElementNode,
    SlackNotFoundError,
)


class MockUIAElement:
    """Mock UIA Element for testing traversal and defensive property handling."""

    def __init__(
        self,
        name: str = "Test Element",
        control_type: str = "Pane",
        automation_id: str = "test-id",
        class_name: str = "TestClass",
        enabled: bool = True,
        visible: bool = True,
        rectangle: tuple = (0, 0, 100, 100),
        process_id: int = 12345,
        children: list | None = None,
        raise_on_attr: str | None = None,
    ):
        self._name = name
        self._control_type = control_type
        self._automation_id = automation_id
        self._class_name = class_name
        self._enabled = enabled
        self._visible = visible
        self._rectangle = rectangle
        self._process_id = process_id
        self._children = children or []
        self._raise_on_attr = raise_on_attr

    @property
    def name(self):
        if self._raise_on_attr == "name":
            raise RuntimeError("COM exception on name")
        return self._name

    @property
    def control_type(self):
        if self._raise_on_attr == "control_type":
            raise RuntimeError("COM exception on control_type")
        return self._control_type

    @property
    def automation_id(self):
        return self._automation_id

    @property
    def class_name(self):
        return self._class_name

    @property
    def enabled(self):
        return self._enabled

    @property
    def visible(self):
        return self._visible

    @property
    def rectangle(self):
        if self._raise_on_attr == "rectangle":
            raise RuntimeError("COM exception on rectangle")
        mock_rect = MagicMock()
        mock_rect.left = self._rectangle[0]
        mock_rect.top = self._rectangle[1]
        mock_rect.right = self._rectangle[2]
        mock_rect.bottom = self._rectangle[3]
        mock_rect.width.return_value = self._rectangle[2] - self._rectangle[0]
        mock_rect.height.return_value = self._rectangle[3] - self._rectangle[1]
        return mock_rect

    @property
    def process_id(self):
        return self._process_id

    def children(self):
        if self._raise_on_attr == "children":
            raise RuntimeError("COM exception on children")
        return self._children


class MockWindow:
    def __init__(self, element_info):
        self.element_info = element_info

    def is_visible(self):
        return True


def test_rectangle_model():
    rect = RectangleModel(left=10, top=20, right=110, bottom=120, width=100, height=100)
    assert rect.width == 100
    assert rect.height == 100
    assert rect.left == 10


def test_slack_element_node_serialization():
    node = SlackElementNode(
        depth=0,
        name="Slack - Alfred",
        control_type="Window",
        automation_id="main-window",
        class_name="Chrome_WidgetWin_1",
        is_enabled=True,
        is_visible=True,
        rectangle=RectangleModel(left=0, top=0, right=1920, bottom=1080, width=1920, height=1080),
        process_id=9999,
        is_truncated=False,
        truncation_reason=None,
        children=[
            SlackElementNode(
                depth=1,
                name="김춘구: 안녕하세요",
                control_type="Text",
                automation_id="",
                class_name="",
                is_enabled=True,
                is_visible=True,
                rectangle=None,
                process_id=9999,
                is_truncated=False,
                truncation_reason=None,
                children=[],
            )
        ],
    )

    data = node.model_dump()
    assert data["depth"] == 0
    assert data["name"] == "Slack - Alfred"
    assert len(data["children"]) == 1
    assert data["children"][0]["name"] == "김춘구: 안녕하세요"
    assert data["children"][0]["depth"] == 1

    # Verify JSON encoding preserves Korean characters
    json_str = json.dumps(data, ensure_ascii=False)
    assert "김춘구: 안녕하세요" in json_str


def test_inspect_tree_mock_hierarchy():
    """Test tree traversal, defensive extraction, and control type counting."""
    child_text = MockUIAElement(name="테스트 메시지", control_type="Text")
    child_button = MockUIAElement(name="작성자", control_type="Button")
    child_broken = MockUIAElement(
        name="Broken",
        control_type="ListItem",
        raise_on_attr="name",
        children=[child_text, child_button],
    )
    root_mock = MockUIAElement(
        name="Slack Main Window",
        control_type="Window",
        children=[child_broken],
    )

    adapter = SlackDesktopAdapter()
    result = adapter.inspect_tree(
        slack_window=MockWindow(root_mock),
        max_depth=5,
        max_elements=100,
    )

    assert isinstance(result, InspectionResult)
    assert result.total_elements == 4
    assert result.max_depth_reached == 2
    assert result.is_truncated is False
    assert result.truncation_reasons == []

    # Check ControlType breakdown counts
    assert result.control_type_counts["Window"] == 1
    assert result.control_type_counts["ListItem"] == 1
    assert result.control_type_counts["Text"] == 1
    assert result.control_type_counts["Button"] == 1


def test_inspect_tree_max_depth_truncation():
    """Test that max_depth stops recursion and marks is_truncated with max_depth_reached."""
    deep_child = MockUIAElement(name="Deep Child", control_type="Text")
    middle_child = MockUIAElement(
        name="Middle Child", control_type="Pane", children=[deep_child]
    )
    root_mock = MockUIAElement(
        name="Root", control_type="Window", children=[middle_child]
    )

    adapter = SlackDesktopAdapter()
    result = adapter.inspect_tree(
        slack_window=MockWindow(root_mock),
        max_depth=1,  # Stop at depth 1
        max_elements=100,
    )

    assert result.is_truncated is True
    assert "max_depth_reached" in result.truncation_reasons
    assert result.total_elements == 2  # Root (0), Middle (1, truncated)
    assert result.root.children[0].is_truncated is True
    assert result.root.children[0].truncation_reason == "max_depth_reached"
    assert len(result.root.children[0].children) == 0


def test_inspect_tree_max_elements_truncation():
    """Test that max_elements limit stops traversal and sets max_elements_reached."""
    children = [MockUIAElement(name=f"Child {i}", control_type="Text") for i in range(10)]
    root_mock = MockUIAElement(
        name="Root", control_type="Window", children=children
    )

    adapter = SlackDesktopAdapter()
    result = adapter.inspect_tree(
        slack_window=MockWindow(root_mock),
        max_depth=5,
        max_elements=4,  # Root counts as 1, so only 3 children extracted
    )

    assert result.is_truncated is True
    assert "max_elements_reached" in result.truncation_reasons
    assert result.total_elements == 4
    assert result.root.is_truncated is True
    assert result.root.truncation_reason == "max_elements_reached"
    assert len(result.root.children) == 3


def test_save_json(tmp_path: Path):
    adapter = SlackDesktopAdapter()
    test_node = SlackElementNode(
        depth=0,
        name="테스트 창",
        control_type="Window",
        process_id=1234,
        children=[],
    )
    output_file = tmp_path / "subdir" / "test_output.json"
    saved = adapter.save_json(test_node, output_file)

    assert saved.exists()
    content = saved.read_text(encoding="utf-8")
    assert "테스트 창" in content
    assert '"process_id": 1234' in content


def test_to_rich_tree():
    adapter = SlackDesktopAdapter()
    root = SlackElementNode(
        depth=0,
        name="Slack Main",
        control_type="Window",
        process_id=5678,
        children=[
            SlackElementNode(
                depth=1,
                name="채널 목록",
                control_type="Tree",
                automation_id="sidebar",
                children=[
                    SlackElementNode(
                        depth=2,
                        name="일반",
                        control_type="TreeItem",
                        children=[],
                    )
                ],
            )
        ],
    )

    rich_tree = adapter.to_rich_tree(root, max_display_depth=5, max_display_elements=20)
    assert rich_tree is not None
    assert "Slack Main" in str(rich_tree.label)


def test_get_desktop_context():
    adapter = SlackDesktopAdapter()
    ctx = adapter.get_desktop_context()
    assert isinstance(ctx, dict)
    assert "session_id" in ctx
    assert "window_station" in ctx
    assert "desktop_name" in ctx


@pytest.mark.live_slack
def test_live_slack_inspection_smoke():
    """Smoke test running on real Slack if Slack is currently available on Windows."""
    adapter = SlackDesktopAdapter()
    try:
        slack_win = adapter.find_slack_window()
    except SlackNotFoundError:
        pytest.skip("Slack이 실행 중이지 않아 Live Slack 스모크 테스트를 건너뜁니다.")

    result = adapter.inspect_tree(slack_win, max_depth=3, max_elements=50)
    assert result.total_elements > 0
    assert result.root.control_type == "Window"
