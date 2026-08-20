"""Slack Conversation Scroll Collector (MVP 2).

현재 Slack 데스크톱 앱에서 열려 있는 대화 컨테이너(채널 / DM / 스레드)를
위 방향으로 안전하게 스크롤하면서 노출되는 메시지를 반복 수집하고,
Scroll-Safe Fingerprint 기반으로 중복 없이 하나의 대화 스트림으로 병합합니다.

핵심 원칙:
- 읽기 전용 및 스크롤 전용: 클릭, 검색, 텍스트 입력, 메시지 전송 등 UI 조작 절대 금지
- 컨테이너 격리: 스크롤 도중 context 또는 conversation_key가 변경되면 즉시 중단
- 결정론적 Overlap Stitching: 과거 방향(위)으로 노출되는 새 메시지를 기존 스트림의 앞(과거)에 순서대로 병합
- 보수적 최상단 판단: 명확한 최상단 도달 근거가 있을 때만 `is_reached_start=True` 설정
"""

from __future__ import annotations

import ctypes
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from pesu_agent.adapters.slack_desktop import (
    InspectionResult,
    SlackDesktopAdapter,
    SlackElementNode,
    SlackNotFoundError,
)
from pesu_agent.parsers.slack_message_parser import (
    SlackMessage,
    SlackMessageParser,
    SlackVisibleMessagesResult,
)

logger = logging.getLogger(__name__)


class SlackConversationCollection(BaseModel):
    """스크롤 수집을 통해 병합된 Slack 대화 스트림 모델."""

    captured_at: str = Field(description="수집 시각 (ISO-8601 UTC)")
    conversation_name: str = Field(description="대화명 (채널/DM/스레드 이름)")
    conversation_key: str = Field(description="대화 식별자")
    context: str = Field(
        description="대화 컨텍스트 ('channel', 'dm', 'thread', 'search_result', 'unknown')",
    )
    scroll_direction: str = Field(default="up", description="스크롤 수집 방향 ('up')")
    scroll_iterations: int = Field(default=0, description="수행한 스크롤 반복 횟수")
    scroll_method: str = Field(
        default="none",
        description="사용된 스크롤 방식 ('uia_scroll_pattern', 'uia_mouse_wheel', 'uia_page_up', 'none')",
    )
    message_count: int = Field(default=0, description="최종 병합된 고유 메시지 총 개수")
    unique_message_count: int = Field(default=0, description="고유 fingerprint 개수")
    first_visible_message: Optional[SlackMessage] = Field(
        default=None,
        description="수집된 가장 오래된 메시지 (과거 기준)",
    )
    last_visible_message: Optional[SlackMessage] = Field(
        default=None,
        description="수집된 가장 최신 메시지 (현재 기준)",
    )
    stop_reason: str = Field(
        default="",
        description="수집 종료 사유 ('reached_start', 'max_scrolls', 'max_messages', 'no_new_messages', 'scroll_not_possible', 'container_lost', 'error')",
    )
    is_reached_start: bool = Field(
        default=False,
        description="대화 최상단 도달 여부 (명확한 증거가 있을 때만 True)",
    )
    is_complete: bool = Field(
        default=False,
        description="전체 대화 완전 수집 여부 (보수적 판단)",
    )
    iteration_stats: list[dict[str, Any]] = Field(
        default_factory=list,
        description="각 스크롤 반복별 수집 통계 (iteration, visible, overlap, new, total_unique)",
    )
    messages: list[SlackMessage] = Field(
        default_factory=list,
        description="시간 순서대로 병합된 메시지 목록 (과거[0] -> 최신[-1])",
    )


class SlackScrollCollector:
    """Slack 대화 컨테이너를 위로 스크롤하며 메시지를 누적 수집하는 컬렉터."""

    def __init__(
        self,
        adapter: Optional[SlackDesktopAdapter] = None,
        parser: Optional[SlackMessageParser] = None,
        scroll_executor: Optional[Callable[[Any, SlackElementNode], tuple[bool, str]]] = None,
        sleep_func: Optional[Callable[[float], None]] = None,
    ):
        self.adapter = adapter or SlackDesktopAdapter()
        self.parser = parser or SlackMessageParser()
        self._custom_scroll_executor = scroll_executor
        self._sleep = sleep_func or time.sleep

    @classmethod
    def _find_message_container_node(
        cls, root_node: SlackElementNode
    ) -> Optional[SlackElementNode]:
        """UIA Tree에서 대화 메시지 목록 컨테이너 노드를 찾습니다."""
        # 1. c-virtual_list__scroll_container 또는 message-list
        def search(n: SlackElementNode) -> Optional[SlackElementNode]:
            cls_name = n.class_name or ""
            aid = n.automation_id or ""
            if "c-virtual_list" in cls_name or "message-list" in aid:
                return n
            for c in n.children:
                res = search(c)
                if res:
                    return res
            return None

        found = search(root_node)
        if found:
            return found

        # 2. List 컨트롤 타입
        def search_list(n: SlackElementNode) -> Optional[SlackElementNode]:
            if n.control_type == "List":
                return n
            for c in n.children:
                res = search_list(c)
                if res:
                    return res
            return None

        return search_list(root_node)

    def execute_scroll_up(
        self,
        slack_window: Any,
        container_node: Optional[SlackElementNode],
        scroll_delta: int = 360,
    ) -> tuple[bool, str]:
        """지정된 메시지 컨테이너 영역에 대해 위 방향 스크롤을 수행합니다."""
        if self._custom_scroll_executor:
            return self._custom_scroll_executor(slack_window, container_node)

        if not slack_window:
            return False, "none"

        # 1. UI Automation ScrollPattern 시도
        try:
            from pywinauto.uia_defines import IUIA

            iuia = IUIA()
            for elem in [slack_window.element_info]:
                pat = elem.element.GetCurrentPattern(10004)  # UIA_ScrollPatternId
                if pat:
                    sp = pat.QueryInterface(
                        iuia.ui_automation_client.IUIAutomationScrollPattern
                    )
                    if sp.CurrentVerticallyScrollable:
                        # ScrollAmount.LargeDecrement = 1 (Up)
                        sp.Scroll(0, 1)
                        return True, "uia_scroll_pattern"
        except Exception:
            pass

        # 2. 메시지 컨테이너 영역 대상 마우스 휠 스크롤 (Primary fallback)
        try:
            user32 = ctypes.windll.user32

            # 컨테이너 바운딩 사각형 좌표 계산
            rect = container_node.rectangle if container_node else None
            if rect and (rect.right - rect.left) > 100 and (rect.bottom - rect.top) > 100:
                center_x = (rect.left + rect.right) // 2
                center_y = (rect.top + rect.bottom) // 2
            else:
                # 윈도우 사각형 중앙
                win_rect = slack_window.rectangle()
                win_l = getattr(win_rect, "left", 0)
                win_r = getattr(win_rect, "right", 0)
                win_t = getattr(win_rect, "top", 0)
                win_b = getattr(win_rect, "bottom", 0)
                center_x = (win_l + win_r) // 2
                center_y = (win_t + win_b) // 2

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            # 사용자 원래 마우스 커서 위치 보존
            orig_pt = POINT()
            user32.GetCursorPos(ctypes.byref(orig_pt))

            # 컨테이너 영역으로 마우스 이동 후 휠 위로 회전 (MOUSEEVENTF_WHEEL = 0x0800)
            user32.SetCursorPos(center_x, center_y)
            user32.mouse_event(0x0800, 0, 0, scroll_delta, 0)
            # 원래 커서 위치 즉시 복원
            user32.SetCursorPos(orig_pt.x, orig_pt.y)

            return True, "uia_mouse_wheel"
        except Exception as err:
            logger.warning(f"마우스 휠 스크롤 실패: {err}")

        # 3. PageUp 키 fallback
        try:
            from pywinauto.keyboard import send_keys

            send_keys("{VK_PRIOR}")
            return True, "uia_page_up"
        except Exception as err:
            logger.warning(f"PageUp 스크롤 실패: {err}")

        return False, "none"

    def collect_conversation(
        self,
        slack_window: Optional[Any] = None,
        max_scrolls: int = 10,
        max_messages: int = 300,
        no_new_message_limit: int = 3,
        settle_ms: int = 800,
        on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> SlackConversationCollection:
        """현재 열린 대화를 위로 스크롤하며 수집합니다."""
        captured_at = datetime.now(timezone.utc).isoformat()

        # 1. Slack 창 탐색 (없는 경우 자동 검색)
        if slack_window is None:
            slack_window = self.adapter.find_slack_window()

        # 2. 초기 뷰포트 UIA Tree 캡처
        initial_tree = self.adapter.inspect_tree(slack_window, max_depth=25, max_elements=3000)
        initial_result: SlackVisibleMessagesResult = self.parser.parse_from_tree(initial_tree)

        initial_context = initial_result.context
        initial_conv_name = initial_result.conversation_name
        initial_conv_key = initial_result.conversation_key

        logger.info(
            f"스크롤 수집 시작: context={initial_context}, conv={initial_conv_name}, "
            f"초기 가시 메시지={initial_result.message_count}개"
        )

        # 수집 스트림 초기화 (시간 순서: 과거[0] -> 최신[-1])
        collected_messages: list[SlackMessage] = list(initial_result.messages)
        seen_fingerprints: set[str] = {m.message_fingerprint for m in collected_messages}

        iteration_stats: list[dict[str, Any]] = [
            {
                "iteration": 0,
                "visible": initial_result.message_count,
                "overlap": 0,
                "new": initial_result.message_count,
                "total_unique": len(seen_fingerprints),
            }
        ]

        if on_progress:
            on_progress(iteration_stats[-1])

        consecutive_no_new = 0
        scroll_iterations = 0
        used_scroll_method = "none"
        stop_reason = ""
        is_reached_start = False

        container_node = self._find_message_container_node(initial_tree.root)

        # 3. 스크롤 반복 루프
        for it in range(1, max_scrolls + 1):
            if len(collected_messages) >= max_messages:
                stop_reason = "max_messages"
                break

            # 스크롤 수행 (위 방향)
            success, method = self.execute_scroll_up(slack_window, container_node)
            used_scroll_method = method
            if not success:
                stop_reason = "scroll_not_possible"
                break

            scroll_iterations = it

            # UI 안정화 대기
            self._sleep(settle_ms / 1000.0)

            # 새로운 뷰포트 UIA Tree 캡처
            try:
                new_tree = self.adapter.inspect_tree(
                    slack_window, max_depth=25, max_elements=3000
                )
                new_result: SlackVisibleMessagesResult = self.parser.parse_from_tree(new_tree)
            except Exception as err:
                logger.error(f"UIA 트리 수집 실패 (Iteration {it}): {err}")
                stop_reason = "error"
                break

            # 안전장치: 대화 컨테이너 변경 여부 검증
            if (
                new_result.conversation_key != initial_conv_key
                or new_result.context != initial_context
            ):
                logger.warning(
                    f"대화 컨테이너 변경 감지 (초기: {initial_conv_key} -> 현재: {new_result.conversation_key}). 수집 즉시 중단."
                )
                stop_reason = "container_lost"
                break

            new_viewport_messages = new_result.messages
            visible_count = len(new_viewport_messages)

            # Overlap 및 새 메시지 판별
            overlap_count = sum(
                1 for m in new_viewport_messages if m.message_fingerprint in seen_fingerprints
            )

            # 뷰포트 상단에서 새로 발견된 과거 메시지들 추출
            new_older_messages: list[SlackMessage] = []
            for m in new_viewport_messages:
                if m.message_fingerprint not in seen_fingerprints:
                    new_older_messages.append(m)
                    seen_fingerprints.add(m.message_fingerprint)

            new_count = len(new_older_messages)

            # 과거 메시지를 기존 스트림의 앞쪽에 prepend
            if new_older_messages:
                collected_messages = new_older_messages + collected_messages
                consecutive_no_new = 0
            else:
                consecutive_no_new += 1

            stat = {
                "iteration": it,
                "visible": visible_count,
                "overlap": overlap_count,
                "new": new_count,
                "total_unique": len(seen_fingerprints),
            }
            iteration_stats.append(stat)

            if on_progress:
                on_progress(stat)

            # 최상단 도달 판단
            # 1. 새 메시지가 연속으로 발견되지 않고 동일 뷰포트가 유지되는 경우
            if consecutive_no_new >= no_new_message_limit:
                # 상단 스페이서 또는 시작 요소 확인
                top_spacer_found = any(
                    "topSpacer" in (c.automation_id or "") or "day_divider" in (c.automation_id or "")
                    for c in (new_tree.root.children if new_tree.root else [])
                )
                if top_spacer_found or it >= 2:
                    is_reached_start = True
                    stop_reason = "reached_start"
                else:
                    stop_reason = "no_new_messages"
                break

        if not stop_reason:
            if scroll_iterations >= max_scrolls:
                stop_reason = "max_scrolls"
            else:
                stop_reason = "reached_start" if is_reached_start else "no_new_messages"

        # 최종 뷰포트 인덱스 재정렬
        for idx, m in enumerate(collected_messages):
            m.viewport_index = idx

        first_msg = collected_messages[0] if collected_messages else None
        last_msg = collected_messages[-1] if collected_messages else None

        collection = SlackConversationCollection(
            captured_at=captured_at,
            conversation_name=initial_conv_name,
            conversation_key=initial_conv_key,
            context=initial_context,
            scroll_direction="up",
            scroll_iterations=scroll_iterations,
            scroll_method=used_scroll_method,
            message_count=len(collected_messages),
            unique_message_count=len(seen_fingerprints),
            first_visible_message=first_msg,
            last_visible_message=last_msg,
            stop_reason=stop_reason,
            is_reached_start=is_reached_start,
            is_complete=is_reached_start and stop_reason == "reached_start",
            iteration_stats=iteration_stats,
            messages=collected_messages,
        )

        logger.info(
            f"스크롤 수집 완료: 총 {len(collected_messages)}개 메시지, {scroll_iterations}회 스크롤, "
            f"종료 사유: {stop_reason}, 최상단 도달: {is_reached_start}"
        )
        return collection

    @staticmethod
    def save_json(collection: SlackConversationCollection, output_path: str | Path) -> Path:
        """수집된 대화 결과를 UTF-8 형식의 JSON 파일로 저장합니다."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(collection.model_dump(), f, ensure_ascii=False, indent=2)

        logger.info(f"대화 수집 JSON 저장 완료: {path.resolve()}")
        return path
