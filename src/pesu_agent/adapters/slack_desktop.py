"""Slack Desktop UI Automation Adapter (MVP 0).

Windows UI Automation (UIA) backend를 활용하여 현재 실행 중인
Slack 데스크톱 애플리케이션의 UI Automation Tree를 안전하게 탐색하고 구조화된 데이터로 추출합니다.

엄격한 읽기 전용(Read-Only) 원칙:
- 어떠한 클릭, 키 입력, 메시지 전송, 토큰/쿠키 접근도 수행하지 않습니다.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SlackNotFoundError(Exception):
    """Slack 데스크톱 애플리케이션 창을 찾을 수 없을 때 발생하는 예외."""

    def __init__(
        self,
        message: str = (
            "Slack 데스크톱 애플리케이션 창을 찾을 수 없습니다.\n"
            "1. Slack이 실행 중인지 확인해 주세요.\n"
            "2. 창이 최소화(트레이) 상태라면 창을 화면에 띄운 후 다시 시도해 주세요.\n"
            "3. Windows 데스크톱 세션에서 접근 가능한 권한인지 확인해 주세요."
        ),
    ):
        super().__init__(message)


class RectangleModel(BaseModel):
    """UI 요소의 화면 좌표 및 크기 정보."""

    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int


class SlackElementNode(BaseModel):
    """UI Automation Tree의 개별 요소 노드."""

    depth: int = Field(description="트리 내 계층 깊이 (0부터 시작)")
    name: str = Field(default="", description="요소 이름 또는 텍스트 본문")
    control_type: str = Field(default="", description="UIA 컨트롤 타입 (Window, Button, Text 등)")
    automation_id: str = Field(default="", description="UIA AutomationId")
    class_name: str = Field(default="", description="클래스명")
    is_enabled: bool = Field(default=False, description="활성화 여부")
    is_visible: bool = Field(default=False, description="가시성 여부")
    rectangle: Optional[RectangleModel] = Field(default=None, description="화면 좌표")
    process_id: int = Field(default=0, description="소유 프로세스 ID")
    is_truncated: bool = Field(
        default=False,
        description="max_depth 또는 max_elements로 인해 하위 자식 탐색이 생략되었는지 여부",
    )
    truncation_reason: Optional[str] = Field(
        default=None,
        description="탐색 중단 원인 ('max_depth_reached', 'max_elements_reached' 또는 None)",
    )
    children: list[SlackElementNode] = Field(default_factory=list, description="하위 자식 요소 목록")


class InspectionResult(BaseModel):
    """UI Automation Tree 검사 전체 결과 및 메타데이터."""

    timestamp: str = Field(description="검사 실행 시각 (ISO-8601)")
    duration_seconds: float = Field(description="탐색 소요 시간 (초)")
    slack_window_title: str = Field(description="탐색된 Slack 창 제목")
    slack_process_id: int = Field(description="탐색된 Slack 메인 프로세스 ID")
    total_elements: int = Field(description="수집된 총 UI 요소 개수")
    max_depth_reached: int = Field(description="실제 도달한 최대 트리 깊이")
    is_truncated: bool = Field(description="탐색 제한으로 인해 트리가 잘렸는지 여부")
    truncation_reasons: list[str] = Field(
        default_factory=list, description="발생한 모든 트리 잘림 원인 목록"
    )
    control_type_counts: dict[str, int] = Field(
        default_factory=dict, description="ControlType별 발견 개수 집계"
    )
    root: SlackElementNode = Field(description="루트 UI 요소 노드")


class SlackDesktopAdapter:
    """Slack 데스크톱 앱의 UI Automation Tree를 검사하는 어댑터."""

    @staticmethod
    def get_desktop_context() -> dict[str, Any]:
        """현재 Windows 프로세스의 Session 및 Window Station/Desktop 정보를 진단용으로 반환합니다."""
        context: dict[str, Any] = {
            "session_id": None,
            "window_station": None,
            "desktop_name": None,
        }
        try:
            import ctypes

            session_id = ctypes.c_ulong()
            if ctypes.windll.kernel32.ProcessIdToSessionId(
                ctypes.windll.kernel32.GetCurrentProcessId(), ctypes.byref(session_id)
            ):
                context["session_id"] = session_id.value

            user32 = ctypes.windll.user32
            winsta = user32.GetProcessWindowStation()
            if winsta:
                buf = ctypes.create_unicode_buffer(256)
                if user32.GetUserObjectInformationW(winsta, 2, buf, ctypes.sizeof(buf), None):
                    context["window_station"] = buf.value

            desk = user32.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
            if desk:
                buf_desk = ctypes.create_unicode_buffer(256)
                if user32.GetUserObjectInformationW(desk, 2, buf_desk, ctypes.sizeof(buf_desk), None):
                    context["desktop_name"] = buf_desk.value
        except Exception as exc:
            logger.debug(f"Failed to query desktop context: {exc}")

        return context

    def find_slack_window(self) -> Any:
        """실행 중인 Slack 데스크톱 애플리케이션의 최상위 창을 찾습니다.

        특정 채널명, 워크스페이스명, 또는 특정 PID를 하드코딩하지 않고,
        프로세스 이름(slack.exe) 및 창 제목/속성을 기반으로 동적으로 탐색합니다.

        Returns:
            pywinauto.application.WindowSpecification 또는 UIA Wrapper 객체

        Raises:
            SlackNotFoundError: Slack 창을 찾을 수 없는 경우
        """
        try:
            from pywinauto import Desktop
        except ImportError as err:
            raise ImportError(
                "pywinauto 라이브러리가 필요합니다. pip install pywinauto 로 설치해 주세요."
            ) from err

        import psutil

        try:
            desktop = Desktop(backend="uia")
            windows = desktop.windows()
        except Exception as exc:
            logger.error(f"UIA Desktop 탐색 실패: {exc}")
            raise SlackNotFoundError(f"UIA Desktop 초기화 중 오류가 발생했습니다: {exc}") from exc

        candidates = []
        for win in windows:
            try:
                elem_info = win.element_info
                name = (elem_info.name or "").strip()
                cls = (elem_info.class_name or "").strip()
                pid = elem_info.process_id

                proc_name = ""
                try:
                    proc_name = psutil.Process(pid).name().lower()
                except Exception:
                    pass

                # Slack 창 식별 조건:
                # 1. 프로세스 이름이 'slack.exe' 또는 'slack' 인 경우
                # 2. 또는 클래스가 Electron(Chrome_WidgetWin_1)이면서 창 제목이 'Slack'이거나 ' - Slack'으로 끝나는 경우
                is_slack_proc = "slack" in proc_name
                is_slack_title = (
                    name == "Slack"
                    or name.endswith(" - Slack")
                    or name.endswith(" – Slack")
                    or name.startswith("Slack |")
                )

                if is_slack_proc or (cls == "Chrome_WidgetWin_1" and is_slack_title):
                    rect = elem_info.rectangle
                    is_vis = win.is_visible()
                    w = rect.width() if rect else 0
                    h = rect.height() if rect else 0

                    # 가중치 계산:
                    # - slack 프로세스이거나 제목 일치 시 우선순위 높임
                    # - 팝업/툴팁/트레이 숨김 창 제외를 위해 유효한 창 크기 및 가시성 가중치 부여
                    score = 0
                    if is_slack_proc:
                        score += 10
                    if is_slack_title:
                        score += 5
                    if is_vis:
                        score += 20
                    if w > 200 and h > 200:
                        score += 20

                    candidates.append((score, win, name, cls, pid, is_vis, w, h))
            except Exception as e:
                logger.debug(f"창 속성 확인 중 건너뜀: {e}")
                continue

        if not candidates:
            ctx = self.get_desktop_context()
            logger.debug(f"Desktop Context: {ctx}")
            raise SlackNotFoundError()

        # 가장 적합한 메인 창 선택 (score 높은 순, 창 면적 큰 순)
        candidates.sort(key=lambda c: (c[0], c[6] * c[7]), reverse=True)
        selected_window = candidates[0][1]
        logger.info(
            f"Slack 창 발견: title='{candidates[0][2]}', pid={candidates[0][4]}, visible={candidates[0][5]}"
        )
        return selected_window

    def inspect_tree(
        self,
        slack_window: Optional[Any] = None,
        max_depth: int = 20,
        max_elements: int = 3000,
    ) -> InspectionResult:
        """Slack 창의 UI Automation Tree를 재귀적으로 탐색하여 구조화된 노드 트리를 반환합니다.

        Args:
            slack_window: 탐색할 Slack 창 객체 (None인 경우 find_slack_window() 자동 호출)
            max_depth: 트리 탐색 최대 깊이
            max_elements: 탐색할 최대 UI 요소 수

        Returns:
            InspectionResult 객체 (루트 노드, 통계, ControlType 집계, 잘림 여부 포함)
        """
        if slack_window is None:
            slack_window = self.find_slack_window()

        start_time = time.time()
        root_elem_info = slack_window.element_info

        element_counter = 0
        max_depth_reached = 0
        control_type_counts: dict[str, int] = {}
        truncation_reasons_set: set[str] = set()

        def extract_node_data(elem: Any, depth: int) -> SlackElementNode:
            nonlocal element_counter, max_depth_reached

            element_counter += 1
            max_depth_reached = max(max_depth_reached, depth)

            # 1. 방어적 속성 추출
            name = ""
            control_type = ""
            automation_id = ""
            class_name = ""
            is_enabled = False
            is_visible = False
            rectangle_model: Optional[RectangleModel] = None
            process_id = 0

            try:
                name = str(elem.name or "")
            except Exception:
                pass

            try:
                control_type = str(elem.control_type or "")
            except Exception:
                pass

            try:
                automation_id = str(elem.automation_id or "")
            except Exception:
                pass

            try:
                class_name = str(elem.class_name or "")
            except Exception:
                pass

            try:
                is_enabled = bool(elem.enabled)
            except Exception:
                pass

            try:
                is_visible = bool(elem.visible)
            except Exception:
                pass

            try:
                rect = elem.rectangle
                if rect is not None:
                    rectangle_model = RectangleModel(
                        left=int(rect.left),
                        top=int(rect.top),
                        right=int(rect.right),
                        bottom=int(rect.bottom),
                        width=int(rect.width()),
                        height=int(rect.height()),
                    )
            except Exception:
                pass

            try:
                process_id = int(elem.process_id or 0)
            except Exception:
                pass

            # ControlType 집계
            ctype_key = control_type if control_type else "Unknown"
            control_type_counts[ctype_key] = control_type_counts.get(ctype_key, 0) + 1

            node = SlackElementNode(
                depth=depth,
                name=name,
                control_type=control_type,
                automation_id=automation_id,
                class_name=class_name,
                is_enabled=is_enabled,
                is_visible=is_visible,
                rectangle=rectangle_model,
                process_id=process_id,
                is_truncated=False,
                truncation_reason=None,
                children=[],
            )

            # 2. 자식 탐색 조건 검사
            if depth >= max_depth:
                node.is_truncated = True
                node.truncation_reason = "max_depth_reached"
                truncation_reasons_set.add("max_depth_reached")
                return node

            # 3. 자식 요소 읽기
            try:
                children_elems = elem.children()
            except Exception as err:
                logger.debug(f"자식 요소 접근 오류 (depth={depth}): {err}")
                children_elems = []

            for child_elem in children_elems:
                if element_counter >= max_elements:
                    node.is_truncated = True
                    node.truncation_reason = "max_elements_reached"
                    truncation_reasons_set.add("max_elements_reached")
                    break

                child_node = extract_node_data(child_elem, depth + 1)
                node.children.append(child_node)

            return node

        root_node = extract_node_data(root_elem_info, depth=0)
        duration = time.time() - start_time

        is_truncated = len(truncation_reasons_set) > 0

        result = InspectionResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration_seconds=round(duration, 3),
            slack_window_title=root_node.name or "Slack",
            slack_process_id=root_node.process_id,
            total_elements=element_counter,
            max_depth_reached=max_depth_reached,
            is_truncated=is_truncated,
            truncation_reasons=sorted(list(truncation_reasons_set)),
            control_type_counts=dict(
                sorted(control_type_counts.items(), key=lambda item: item[1], reverse=True)
            ),
            root=root_node,
        )

        return result

    @staticmethod
    def save_json(
        data: InspectionResult | SlackElementNode | dict[str, Any],
        file_path: str | Path,
    ) -> Path:
        """결과 데이터를 UTF-8 형식의 JSON 파일로 저장합니다.

        한글 텍스트가 유니코드 이스케이프 없이 사람이 읽기 쉬운 형태로 보존됩니다.

        Args:
            data: 저장할 데이터 모델 또는 딕셔너리
            file_path: 대상 파일 경로

        Returns:
            저장된 파일의 Path 객체
        """
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, BaseModel):
            dict_data = data.model_dump()
        else:
            dict_data = data

        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict_data, f, ensure_ascii=False, indent=2)

        logger.info(f"JSON 파일 저장 완료: {path.resolve()}")
        return path

    @staticmethod
    def to_rich_tree(
        node: SlackElementNode,
        max_display_depth: Optional[int] = None,
        max_display_elements: Optional[int] = None,
    ) -> Any:
        """SlackElementNode를 사람이 보기 쉬운 rich.tree.Tree 객체로 변환합니다."""
        try:
            from rich.tree import Tree
        except ImportError as err:
            raise ImportError("rich 라이브러리가 필요합니다. pip install rich 로 설치해 주세요.") from err

        displayed_count = 0

        # ControlType별 테마 색상 지정
        type_colors: dict[str, str] = {
            "Window": "bold yellow",
            "Document": "bold blue",
            "Pane": "dim cyan",
            "Group": "bright_black",
            "List": "bold magenta",
            "ListItem": "magenta",
            "Button": "bold green",
            "Text": "bright_white",
            "Hyperlink": "bright_cyan",
            "ComboBox": "bold blue",
            "Edit": "bold yellow",
            "Tree": "bold blue",
            "TreeItem": "blue",
            "Tab": "cyan",
            "TabItem": "bright_blue",
            "ToolBar": "dim yellow",
        }

        def build_label(n: SlackElementNode) -> str:
            ctype = n.control_type or "Unknown"
            color = type_colors.get(ctype, "white")
            parts = [f"[{color}]{ctype}[/{color}]"]

            clean_name = (n.name or "").replace("\r", " ").replace("\n", " ").strip()
            if clean_name:
                if len(clean_name) > 60:
                    clean_name = clean_name[:57] + "..."
                parts.append(f": [white]{clean_name}[/white]")

            extra_info = []
            if n.automation_id:
                extra_info.append(f"id={n.automation_id}")
            if n.class_name and n.class_name not in ("", "View", "Intermediate D3D Window"):
                cls_str = n.class_name
                if len(cls_str) > 25:
                    cls_str = cls_str[:22] + "..."
                extra_info.append(f"cls={cls_str}")

            if extra_info:
                parts.append(f" [dim]({', '.join(extra_info)})[/dim]")

            if n.is_truncated:
                reason = n.truncation_reason or "truncated"
                parts.append(f" [bold red][TRUNCATED: {reason}][/bold red]")

            return "".join(parts)

        root_label = (
            f"[bold green]Slack Window[/bold green]: {node.name or 'Slack'} "
            f"[dim](PID: {node.process_id})[/dim]"
        )
        tree = Tree(root_label)
        displayed_count += 1

        def add_children(parent_node: SlackElementNode, current_branch: Any, current_depth: int):
            nonlocal displayed_count

            if max_display_depth is not None and current_depth > max_display_depth:
                current_branch.add("[dim italic]... (console display max-depth reached)[/dim italic]")
                return

            for child in parent_node.children:
                if max_display_elements is not None and displayed_count >= max_display_elements:
                    current_branch.add(
                        "[dim italic]... (console display max-elements reached)[/dim italic]"
                    )
                    return

                displayed_count += 1
                branch = current_branch.add(build_label(child))
                add_children(child, branch, current_depth + 1)

        add_children(node, tree, 1)
        return tree
