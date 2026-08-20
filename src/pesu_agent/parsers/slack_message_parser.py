"""Slack Visible Message Parser (MVP 1).

UI Automation Tree로부터 현재 Slack 화면에 노출된 메시지를 파싱하여
구조화된 SlackMessage 객체 목록으로 정규화합니다.

주의:
- UI Virtualization으로 인해 화면에 렌더링된 메시지만 수집되며,
  '전체 대화'가 아닌 '현재 화면에 보이는 메시지'입니다.
- 특정 회사/채널명/사용자명/메시지 본문 등을 하드코딩하지 않습니다.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from pesu_agent.adapters.slack_desktop import InspectionResult, SlackElementNode

logger = logging.getLogger(__name__)


class SlackMessage(BaseModel):
    """정규화된 단일 Slack 메시지 모델."""

    author: Optional[str] = Field(default=None, description="메시지 작성자명 (연속 메시지 시 None 가능)")
    timestamp_raw: Optional[str] = Field(
        default=None,
        description="Slack UI 원본 타임스탬프 텍스트 (예: '어제, 오후 3:29:01', '오늘, 오후 12:01:52')",
    )
    text: str = Field(default="", description="정규화된 메시지 본문 텍스트")
    mentions: list[str] = Field(
        default_factory=list,
        description="본문에 포함된 멘션 목록 (예: ['@홍길동', '@channel'])",
    )
    links: list[str] = Field(
        default_factory=list,
        description="본문에 포함된 웹 URL 링크 목록",
    )
    context: str = Field(
        default="unknown",
        description="메시지 컨텍스트 ('channel', 'thread', 'search_result', 'unknown')",
    )
    source_node_name: str = Field(
        default="",
        description="UIA 소스 ListItem 노드의 원본 이름",
    )
    tree_depth: int = Field(
        default=0,
        description="UIA 트리 계층 깊이",
    )


class SlackVisibleMessagesResult(BaseModel):
    """현재 화면에 노출된 가시적 Slack 메시지 파싱 결과."""

    captured_at: str = Field(description="추출 시각 (ISO-8601 UTC)")
    slack_window_title: str = Field(description="Slack 창 제목")
    message_count: int = Field(description="추출된 메시지 수")
    scope: str = Field(
        default="visible_uia_only",
        description="데이터 추출 범위 (화면 노출 메시지만 해당)",
    )
    is_complete_conversation: bool = Field(
        default=False,
        description="가상화로 인해 전체 대화가 아님을 나타내는 플래그",
    )
    excluded_candidates_count: int = Field(
        default=0,
        description="구분선, 공백, 비메시지 요소 등으로 파서가 제외한 후보 개수",
    )
    messages: list[SlackMessage] = Field(
        default_factory=list,
        description="파싱된 메시지 목록",
    )


class SlackMessageParser:
    """UIA Tree 노드로부터 Slack 메시지를 추출하는 파서."""

    # 타임스탬프 판별 정규식 (한국어/영어 Slack UI 지원)
    _TIMESTAMP_PATTERN = re.compile(
        r"(오늘|어제|오전|오후|\bAM\b|\bPM\b|\byesterday\b|\btoday\b|\d{1,2}:\d{2}(?::\d{2})?)",
        re.IGNORECASE,
    )

    # 비메시지 제어 요소/구분선 식별 키워드
    _NON_MESSAGE_ID_KEYWORDS = (
        "unreadDivider",
        "bottomSpacer",
        "topSpacer",
        "date_divider",
        "day_divider",
        "sidebar",
    )

    # 메시지 작성자 버튼으로 취급하지 않을 UI 액션 버튼명
    _ACTION_BUTTON_KEYWORDS = (
        "반응",
        "댓글",
        "스레드",
        "더 많은",
        "공유",
        "북마크",
        "핀",
        "최소화",
        "최대화",
        "복구",
        "닫기",
        "Slack",
        "검색",
        "설정",
        "작업",
        "날짜로 이동",
        "이동",
        "점프",
        "새 메시지",
        "사이드바",
    )

    @classmethod
    def is_timestamp_element(cls, name: str, class_name: str) -> bool:
        """해당 요소가 타임스탬프를 나타내는지 여부를 판별합니다."""
        if not name:
            return False
        if "timestamp" in class_name.lower():
            return True
        if "채널에서 열기" in name or "스레드에서 열기" in name:
            return True
        return bool(cls._TIMESTAMP_PATTERN.search(name))

    @classmethod
    def is_mention_element(cls, name: str, class_name: str) -> bool:
        """해당 요소가 @멘션을 나타내는지 여부를 판별합니다."""
        if not name:
            return False
        if name.startswith("@"):
            return True
        if "member_slug" in class_name.lower():
            return True
        return False

    @classmethod
    def is_url_element(cls, name: str) -> bool:
        """해당 요소가 외부 URL 링크를 나타내는지 여부를 판별합니다."""
        if not name:
            return False
        return name.startswith("http://") or name.startswith("https://")

    @classmethod
    def determine_context(
        cls,
        node: SlackElementNode,
        ancestor_path: list[SlackElementNode],
        window_title: str,
    ) -> str:
        """메시지가 위치한 컨텍스트(channel, thread, search_result, unknown)를 판별합니다."""
        # 1. 조상 노드의 id 또는 class_name 확인
        for anc in reversed(ancestor_path):
            anc_id = (anc.automation_id or "").lower()
            anc_cls = (anc.class_name or "").lower()
            anc_name = (anc.name or "").lower()

            if "thread" in anc_id or "thread" in anc_cls or "thread" in anc_name:
                return "thread"
            if "search" in anc_id or "search" in anc_cls or "search" in anc_name:
                return "search_result"
            if "message_pane" in anc_cls or "message-list" in anc_id or "channel" in anc_cls:
                return "channel"

        # 2. 창 제목 기반 힌트
        title_lower = window_title.lower()
        if "스레드" in title_lower or "thread" in title_lower:
            return "thread"
        if "검색" in title_lower or "search" in title_lower:
            return "search_result"
        if "(채널)" in window_title or "#" in window_title or "slack" in title_lower:
            return "channel"

        return "unknown"

    def is_non_message_candidate(self, node: SlackElementNode) -> bool:
        """후보 ListItem이 메시지가 아닌 UI 구분선/공백/시스템 컨트롤인지 판별합니다."""
        aid = node.automation_id or ""
        name = (node.name or "").strip()

        # 1. 자동화 ID 기반 제외
        for kw in self._NON_MESSAGE_ID_KEYWORDS:
            if kw in aid:
                return True

        # 2. 이름 기반 제외 (새 항목 구분선, 빈 항목 등)
        if name in ("새 항목", "Unread messages", "New items", ""):
            if not node.children:
                return True
            # 자식 노드가 없거나 자식 모두 비어 있는 경우
            has_sub_content = any(c.name for c in node.children)
            if not has_sub_content:
                return True

        return False

    def parse_message_container(
        self,
        container_node: SlackElementNode,
        context: str,
    ) -> Optional[SlackMessage]:
        """단일 메시지 컨테이너(ListItem 등)의 subtree를 순회하여 SlackMessage를 추출합니다.

        서로 다른 메시지의 작성자, 시간, 본문이 섞이지 않도록
        오직 container_node의 subtree 내부에서만 값을 수집합니다.
        """
        if self.is_non_message_candidate(container_node):
            return None

        author: Optional[str] = None
        timestamp_raw: Optional[str] = None
        mentions: list[str] = []
        links: list[str] = []
        text_segments: list[str] = []

        def walk_subtree(n: SlackElementNode):
            nonlocal author, timestamp_raw

            ctype = n.control_type or ""
            n_name = (n.name or "").strip()
            n_cls = n.class_name or ""

            # 1. 작성자 (Button) 탐색
            if ctype == "Button":
                is_sender_button = (
                    "sender_button" in n_cls
                    or "message__sender" in n_cls
                    or "sender" in n_cls
                )
                is_action_btn = any(kw in n_name for kw in self._ACTION_BUTTON_KEYWORDS)

                if is_sender_button or (not author and n_name and not is_action_btn):
                    if not author and not n_name.startswith("@"):
                        author = n_name

            # 2. 타임스탬프 (Hyperlink) 탐색
            elif ctype == "Hyperlink":
                if self.is_timestamp_element(n_name, n_cls):
                    if not timestamp_raw:
                        timestamp_raw = n_name
                elif self.is_mention_element(n_name, n_cls):
                    # 멘션
                    mention_text = n_name if n_name.startswith("@") else f"@{n_name}"
                    if mention_text not in mentions:
                        mentions.append(mention_text)
                    text_segments.append(mention_text)
                elif self.is_url_element(n_name):
                    # 링크
                    if n_name not in links:
                        links.append(n_name)
                    text_segments.append(n_name)
                else:
                    # 일반 링크 텍스트
                    if n_name and not any(kw in n_name for kw in self._ACTION_BUTTON_KEYWORDS):
                        text_segments.append(n_name)

            # 3. 본문 텍스트 (Text) 탐색
            elif ctype == "Text":
                # UI 배지(예: '앱', 'BOT')나 타임스탬프 중복 제외
                if n_name and n_name not in ("앱", "APP", "BOT"):
                    if not self.is_timestamp_element(n_name, n_cls):
                        if self.is_mention_element(n_name, n_cls):
                            mention_text = n_name if n_name.startswith("@") else f"@{n_name}"
                            if mention_text not in mentions:
                                mentions.append(mention_text)
                            text_segments.append(mention_text)
                        else:
                            text_segments.append(n_name)

            # 자식 노드 재귀 탐색 (동일 subtree 내부)
            for child in n.children:
                walk_subtree(child)

        walk_subtree(container_node)

        # 본문 텍스트 결합 및 정규화
        cleaned_text = " ".join(part for part in text_segments if part).strip()
        # 중복 공백 정리
        cleaned_text = re.sub(r"\s+", " ", cleaned_text)

        # 본문이 비어있으면 (링크나 멘션도 없음) 유효한 메시지가 아님
        if not cleaned_text and not links:
            return None

        # ListItem의 원본 이름을 안전하게 확보
        source_name = (container_node.name or "").replace("\r", " ").replace("\n", " ").strip()

        return SlackMessage(
            author=author,
            timestamp_raw=timestamp_raw,
            text=cleaned_text,
            mentions=mentions,
            links=links,
            context=context,
            source_node_name=source_name,
            tree_depth=container_node.depth,
        )

    def parse_from_tree(
        self,
        tree_input: InspectionResult | SlackElementNode,
        window_title: Optional[str] = None,
    ) -> SlackVisibleMessagesResult:
        """UIA InspectionResult 또는 SlackElementNode 트리로부터 가시적 메시지 목록을 추출합니다."""
        if isinstance(tree_input, InspectionResult):
            root_node = tree_input.root
            resolved_title = tree_input.slack_window_title
            captured_at = tree_input.timestamp
        else:
            root_node = tree_input
            resolved_title = window_title or (root_node.name if root_node else "Slack")
            captured_at = datetime.now(timezone.utc).isoformat()

        # 1. 메시지 컨테이너 후보 수집 (ListItem 위주)
        candidate_containers: list[tuple[SlackElementNode, list[SlackElementNode]]] = []

        def collect_candidates(node: SlackElementNode, path: list[SlackElementNode]):
            # ListItem을 메시지 컨테이너의 기본 단위로 수집
            if node.control_type == "ListItem":
                # 사이드바 채널 TreeItem 내부에 잘못 포함된 항목이 아닌지 확인
                is_sidebar = any(anc.control_type == "Tree" for anc in path)
                if not is_sidebar:
                    candidate_containers.append((node, list(path)))

            for child in node.children:
                collect_candidates(child, path + [node])

        collect_candidates(root_node, [])

        messages: list[SlackMessage] = []
        excluded_count = 0

        for container_node, ancestor_path in candidate_containers:
            ctx = self.determine_context(container_node, ancestor_path, resolved_title)
            msg = self.parse_message_container(container_node, context=ctx)
            if msg is not None:
                messages.append(msg)
            else:
                excluded_count += 1

        result = SlackVisibleMessagesResult(
            captured_at=captured_at,
            slack_window_title=resolved_title,
            message_count=len(messages),
            scope="visible_uia_only",
            is_complete_conversation=False,
            excluded_candidates_count=excluded_count,
            messages=messages,
        )

        logger.info(
            f"메시지 파싱 완료: {len(messages)}개 메시지 추출, {excluded_count}개 후보 제외"
        )
        return result

    def parse_from_json(self, json_path: str | Path) -> SlackVisibleMessagesResult:
        """저장된 UIA Tree JSON 파일(예: output/slack_uia_tree.json)을 읽어 메시지를 파싱합니다."""
        path = Path(json_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "root" in data:
            inspection_result = InspectionResult.model_validate(data)
            return self.parse_from_tree(inspection_result)
        else:
            root_node = SlackElementNode.model_validate(data)
            return self.parse_from_tree(root_node)

    @staticmethod
    def save_json(result: SlackVisibleMessagesResult, output_path: str | Path) -> Path:
        """파싱된 메시지 결과를 UTF-8 형식의 JSON 파일로 저장합니다."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(), f, ensure_ascii=False, indent=2)

        logger.info(f"가시적 메시지 JSON 저장 완료: {path.resolve()}")
        return path
