"""Slack Visible Message Parser (MVP 1.2 & MVP 2: Scroll-Safe Message Identity).

UI Automation Tree로부터 현재 Slack 화면에 노출된 메시지를 파싱하여
구조화된 SlackMessage 객체 목록으로 정규화하고,
스크롤 세션 간 안정적인 메시지 식별(Scroll-Safe Message Identity)을 보장합니다.

MVP 2 개선사항:
- 컨텍스트 정밀 판별 (`dm`, `channel`, `thread`, `search_result`, `unknown`)
- `uia_message_id`: UIA AutomationId에서 추출된 타임스탬프 ID 후보 보존 (예: '1771892661.466919')
- `conversation_name`, `conversation_key` 식별자 추가
- Scroll-Safe Fingerprint 불변성 유지
"""

from __future__ import annotations

import hashlib
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

    viewport_index: int = Field(
        default=0,
        description="현재 뷰포트/화면 내 메시지 표시 순서 (0부터 시작, 뷰포트별 로컬 인덱스)",
    )
    author: Optional[str] = Field(
        default=None,
        description="하위 호환성을 위한 작성자명 (author_resolved와 동일)",
    )
    author_raw: Optional[str] = Field(
        default=None,
        description="UIA subtree에서 직접 발견된 원본 작성자명",
    )
    author_resolved: Optional[str] = Field(
        default=None,
        description="보정된 작성자명 (직접 발견 또는 직전 메시지 상속)",
    )
    author_resolution: str = Field(
        default="unresolved",
        description="작성자 보정 상태 ('explicit', 'inherited_from_previous_message', 'unresolved')",
    )
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
        description="메시지 컨텍스트 ('channel', 'dm', 'thread', 'search_result', 'unknown')",
    )
    source_container: str = Field(
        default="",
        description="메시지가 속한 상위 컨테이너/목록 식별자",
    )
    source_node_name: str = Field(
        default="",
        description="UIA 소스 ListItem 노드의 원본 이름",
    )
    tree_depth: int = Field(
        default=0,
        description="UIA 트리 계층 깊이",
    )
    uia_message_id: Optional[str] = Field(
        default=None,
        description="UIA AutomationId에서 추출된 타임스탬프 ID 후보 (예: '1771892661.466919', Slack 공식 ID 아님)",
    )
    conversation_key: str = Field(
        default="",
        description="현재 대화 컨테이너 고유 식별자",
    )
    conversation_name: str = Field(
        default="",
        description="현재 대화/채널/DM 이름",
    )
    message_fingerprint: str = Field(
        default="",
        description="스크롤 중복 제거용 Scroll-Safe SHA-256 해시 (Slack 공식 서버 ID가 아닌 로컬 세션 식별자)",
    )


class SlackVisibleMessagesResult(BaseModel):
    """현재 화면에 노출된 가시적 Slack 메시지 파싱 결과."""

    captured_at: str = Field(description="추출 시각 (ISO-8601 UTC)")
    slack_window_title: str = Field(description="Slack 창 제목")
    conversation_name: str = Field(default="", description="현재 대화/채널/DM 이름")
    conversation_key: str = Field(default="", description="현재 대화 컨테이너 고유 식별자")
    context: str = Field(
        default="unknown",
        description="현재 대화 컨텍스트 ('channel', 'dm', 'thread', 'search_result', 'unknown')",
    )
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
    explicit_author_count: int = Field(
        default=0,
        description="UIA에서 직접 작성자가 발견된 메시지 수",
    )
    inherited_author_count: int = Field(
        default=0,
        description="동일 컨테이너 직전 메시지로부터 작성자가 상속된 메시지 수",
    )
    unresolved_author_count: int = Field(
        default=0,
        description="작성자를 확인할 수 없는 메시지 수",
    )
    unique_fingerprints_count: int = Field(
        default=0,
        description="고유한 fingerprint 개수",
    )
    duplicate_fingerprint_groups_count: int = Field(
        default=0,
        description="2회 이상 중복 등장한 fingerprint 그룹 수",
    )
    messages: list[SlackMessage] = Field(
        default_factory=list,
        description="파싱된 메시지 목록",
    )


class SlackMessageParser:
    """UIA Tree 노드로부터 Slack 메시지를 추출하고 Scroll-Safe Identity를 생성하는 파서."""

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
    def extract_uia_message_id(cls, automation_id: str) -> Optional[str]:
        """Slack UIA automation_id (예: 'message-list_1771892661.466919')에서 타임스탬프 ID 후보를 추출합니다."""
        if not automation_id:
            return None
        match = re.search(r"message-list_(\d+\.\d+)", automation_id)
        if match:
            return match.group(1)
        return None

    @classmethod
    def compute_fingerprint(
        cls,
        context: str,
        timestamp_raw: Optional[str],
        text: str,
        mentions: list[str],
        links: list[str],
        uia_message_id: Optional[str] = None,
    ) -> str:
        """스크롤 세션 간 안정적인 Scroll-Safe SHA-256 fingerprint를 생성합니다.

        중요한 설계 원칙:
        - `author_resolved`는 스크롤/뷰포트 위치에 따라 첫 가시 메시지 여부가 바뀌며 달라질 수 있으므로
          fingerprint 해시 입력에서 의도적으로 제외합니다.
        - 오직 UIA에서 메시지 자체에 직접 노출되는 불변 속성만 사용합니다.

        주의:
        - 이것은 Slack 서버의 공식 메시지 ID가 아니라 로컬 세션 중복 제거용 식별자입니다.
        """
        norm_mentions = ",".join(sorted(mentions))
        norm_links = ",".join(sorted(links))
        norm_text = re.sub(r"\s+", " ", text).strip()
        uia_id_str = uia_message_id.strip() if uia_message_id else ""

        raw_payload = (
            f"{context.strip()}|"
            f"{timestamp_raw or ''}|"
            f"{norm_text}|"
            f"{norm_mentions}|"
            f"{norm_links}|"
            f"{uia_id_str}"
        )
        return hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

    @classmethod
    def parse_conversation_info(
        cls,
        window_title: str,
        ancestor_path: list[SlackElementNode],
    ) -> tuple[str, str, str]:
        """(context, conversation_name, conversation_key)를 정밀 판별합니다."""
        title_lower = window_title.lower()

        # 1. DM 판별: 제목에 (DM), (다이렉트 메시지), 또는 조상에 dm/im 관련 클래스/이름
        has_dm_marker = (
            "(dm)" in title_lower
            or "(다이렉트 메시지)" in window_title
            or "다이렉트 메시지" in window_title
            or any("다이렉트 메시지" in (anc.name or "") for anc in ancestor_path)
            or any("im_browser" in (anc.class_name or "") for anc in ancestor_path)
        )

        # 2. Thread 판별
        has_thread_marker = (
            "스레드" in window_title
            or "thread" in title_lower
            or any("thread" in (anc.automation_id or "").lower() for anc in ancestor_path)
            or any("thread" in (anc.class_name or "").lower() for anc in ancestor_path)
            or any("스레드" in (anc.name or "") for anc in ancestor_path)
        )

        # 3. Search 판별
        has_search_marker = (
            "검색" in window_title
            or "search" in title_lower
            or any("search" in (anc.automation_id or "").lower() for anc in ancestor_path)
        )

        # 4. Channel 판별
        has_channel_marker = (
            "(채널)" in window_title
            or "#" in window_title
            or "(공유 채널)" in window_title
            or any("channel" in (anc.class_name or "").lower() for anc in ancestor_path)
        )

        if has_thread_marker:
            context = "thread"
        elif has_search_marker:
            context = "search_result"
        elif has_dm_marker:
            context = "dm"
        elif has_channel_marker:
            context = "channel"
        else:
            context = "unknown"

        # Conversation Name 정제
        conversation_name = window_title.split(" - Slack")[0].split(" - ")[0].strip()
        if not conversation_name or conversation_name == "Slack":
            for anc in reversed(ancestor_path):
                if anc.control_type in ("List", "Pane") and anc.name and anc.name != "Slack":
                    conversation_name = anc.name.split(" (")[0].strip()
                    break

        conversation_key = f"{context}:{conversation_name or 'default'}"
        return context, conversation_name, conversation_key

    @classmethod
    def determine_context(
        cls,
        node: SlackElementNode,
        ancestor_path: list[SlackElementNode],
        window_title: str,
    ) -> str:
        """메시지가 위치한 컨텍스트(dm, channel, thread, search_result, unknown)를 판별합니다."""
        context, _, _ = cls.parse_conversation_info(window_title, ancestor_path)
        return context

    @classmethod
    def get_container_identifier(cls, ancestor_path: list[SlackElementNode]) -> str:
        """메시지 컨테이너(List/Tree/Pane 등)의 고유 식별자를 생성합니다."""
        for anc in reversed(ancestor_path):
            if anc.control_type in ("List", "Tree", "Document", "Pane", "Group"):
                aid = anc.automation_id or ""
                cls_name = anc.class_name or ""
                name = (anc.name or "")[:30]
                if aid or cls_name or name:
                    return f"{anc.control_type}:{aid or cls_name or name}:{anc.depth}"
        return "default_container"

    def is_non_message_candidate(self, node: SlackElementNode) -> bool:
        """후보 ListItem이 메시지가 아닌 UI 구분선/공백/시스템 컨트롤인지 판별합니다."""
        aid = node.automation_id or ""
        name = (node.name or "").strip()

        for kw in self._NON_MESSAGE_ID_KEYWORDS:
            if kw in aid:
                return True

        if name in ("새 항목", "Unread messages", "New items", "새 메시지", ""):
            sub_texts = [c.name.strip() for c in node.children if c.name]
            if not sub_texts or all(
                t in ("새 항목", "새 메시지", "Unread messages", "New items", "") for t in sub_texts
            ):
                return True

        return False

    def parse_raw_message_container(
        self,
        container_node: SlackElementNode,
        context: str,
        source_container: str,
        conversation_name: str = "",
        conversation_key: str = "",
    ) -> Optional[SlackMessage]:
        """단일 메시지 컨테이너(ListItem 등)의 subtree를 순회하여 원본 속성을 추출합니다."""
        if self.is_non_message_candidate(container_node):
            return None

        author_raw: Optional[str] = None
        timestamp_raw: Optional[str] = None
        mentions: list[str] = []
        links: list[str] = []
        text_segments: list[str] = []

        def walk_subtree(n: SlackElementNode):
            nonlocal author_raw, timestamp_raw

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

                if is_sender_button or (not author_raw and n_name and not is_action_btn):
                    if not author_raw and not n_name.startswith("@"):
                        author_raw = n_name

            # 2. 타임스탬프 (Hyperlink) 탐색
            elif ctype == "Hyperlink":
                if self.is_timestamp_element(n_name, n_cls):
                    if not timestamp_raw:
                        timestamp_raw = n_name
                elif self.is_mention_element(n_name, n_cls):
                    mention_text = n_name if n_name.startswith("@") else f"@{n_name}"
                    if mention_text not in mentions:
                        mentions.append(mention_text)
                    text_segments.append(mention_text)
                elif self.is_url_element(n_name):
                    if n_name not in links:
                        links.append(n_name)
                    text_segments.append(n_name)
                else:
                    if n_name and not any(kw in n_name for kw in self._ACTION_BUTTON_KEYWORDS):
                        text_segments.append(n_name)

            # 3. 본문 텍스트 (Text 및 ListItem 글머리 기호 텍스트) 탐색
            elif ctype == "Text":
                if n_name and n_name not in ("앱", "APP", "BOT"):
                    if not self.is_timestamp_element(n_name, n_cls):
                        if self.is_mention_element(n_name, n_cls):
                            mention_text = n_name if n_name.startswith("@") else f"@{n_name}"
                            if mention_text not in mentions:
                                mentions.append(mention_text)
                            text_segments.append(mention_text)
                        else:
                            text_segments.append(n_name)
            elif ctype == "ListItem":
                # 중첩 글머리 기호 ListItem인 경우 해당 텍스트를 본문에 포함
                if n_name and not self.is_timestamp_element(n_name, n_cls):
                    text_segments.append(n_name)

            for child in n.children:
                walk_subtree(child)

        walk_subtree(container_node)

        # 본문 텍스트 결합 및 정규화
        cleaned_text = " ".join(part for part in text_segments if part).strip()
        cleaned_text = re.sub(r"\s+", " ", cleaned_text)

        # 본문이 비어있거나 순수 구분선 라벨인 경우 제외
        if not cleaned_text and not links:
            return None
        if cleaned_text in ("새 항목", "새 메시지", "Unread messages", "New items") and not links:
            return None

        source_name = (container_node.name or "").replace("\r", " ").replace("\n", " ").strip()
        uia_ts_id = self.extract_uia_message_id(container_node.automation_id)

        return SlackMessage(
            viewport_index=0,
            author=author_raw,
            author_raw=author_raw,
            author_resolved=author_raw,
            author_resolution="explicit" if author_raw else "unresolved",
            timestamp_raw=timestamp_raw,
            text=cleaned_text,
            mentions=mentions,
            links=links,
            context=context,
            source_container=source_container,
            source_node_name=source_name,
            tree_depth=container_node.depth,
            uia_message_id=uia_ts_id,
            conversation_name=conversation_name,
            conversation_key=conversation_key,
            message_fingerprint="",
        )

    def parse_from_tree(
        self,
        tree_input: InspectionResult | SlackElementNode,
        window_title: Optional[str] = None,
    ) -> SlackVisibleMessagesResult:
        """UIA InspectionResult 또는 SlackElementNode 트리로부터 가시적 메시지 목록을 추출하고 보정합니다."""
        if isinstance(tree_input, InspectionResult):
            root_node = tree_input.root
            resolved_title = tree_input.slack_window_title
            captured_at = tree_input.timestamp
        else:
            root_node = tree_input
            resolved_title = window_title or (root_node.name if root_node else "Slack")
            captured_at = datetime.now(timezone.utc).isoformat()

        # 대화 정보 분석
        context, conv_name, conv_key = self.parse_conversation_info(resolved_title, [])

        # 1. 메시지 컨테이너 후보 수집 (최상위 메시지 ListItem 위주)
        candidate_containers: list[tuple[SlackElementNode, list[SlackElementNode]]] = []

        def collect_candidates(node: SlackElementNode, path: list[SlackElementNode]):
            if node.control_type == "ListItem":
                is_sidebar = any(anc.control_type == "Tree" for anc in path)
                has_parent_list_item = any(anc.control_type == "ListItem" for anc in path)
                if not is_sidebar and not has_parent_list_item:
                    candidate_containers.append((node, list(path)))

            for child in node.children:
                collect_candidates(child, path + [node])

        collect_candidates(root_node, [])

        messages: list[SlackMessage] = []
        excluded_count = 0

        # 컨테이너 스트림별 직전 작성자 추적 (경계를 넘는 상속 방지)
        last_author_by_container: dict[str, str] = {}

        for container_node, ancestor_path in candidate_containers:
            node_ctx, node_conv_name, node_conv_key = self.parse_conversation_info(
                resolved_title, ancestor_path
            )
            container_id = self.get_container_identifier(ancestor_path)

            msg = self.parse_raw_message_container(
                container_node,
                context=node_ctx,
                source_container=container_id,
                conversation_name=node_conv_name,
                conversation_key=node_conv_key,
            )

            if msg is None:
                excluded_count += 1
                continue

            # 뷰포트 내 표시 순서 기록 (0부터 시작)
            msg.viewport_index = len(messages)

            # 작성자 보정 (Author Resolution)
            if msg.author_raw is not None:
                msg.author_resolved = msg.author_raw
                msg.author_resolution = "explicit"
                last_author_by_container[container_id] = msg.author_raw
            elif container_id in last_author_by_container:
                msg.author_resolved = last_author_by_container[container_id]
                msg.author_resolution = "inherited_from_previous_message"
            else:
                msg.author_resolved = None
                msg.author_resolution = "unresolved"

            # 하위 호환성을 위해 author 필드 동기화
            msg.author = msg.author_resolved

            # Scroll-Safe Fingerprint 생성 (author_resolved 제외)
            msg.message_fingerprint = self.compute_fingerprint(
                context=msg.context,
                timestamp_raw=msg.timestamp_raw,
                text=msg.text,
                mentions=msg.mentions,
                links=msg.links,
                uia_message_id=msg.uia_message_id,
            )

            messages.append(msg)

        # 통계 집계
        explicit_count = sum(1 for m in messages if m.author_resolution == "explicit")
        inherited_count = sum(
            1 for m in messages if m.author_resolution == "inherited_from_previous_message"
        )
        unresolved_count = sum(1 for m in messages if m.author_resolution == "unresolved")

        fp_map: dict[str, list[SlackMessage]] = {}
        for m in messages:
            fp_map.setdefault(m.message_fingerprint, []).append(m)

        unique_fingerprints_count = len(fp_map)
        duplicate_groups_count = sum(1 for group in fp_map.values() if len(group) > 1)

        result = SlackVisibleMessagesResult(
            captured_at=captured_at,
            slack_window_title=resolved_title,
            conversation_name=conv_name,
            conversation_key=conv_key,
            context=context,
            message_count=len(messages),
            scope="visible_uia_only",
            is_complete_conversation=False,
            excluded_candidates_count=excluded_count,
            explicit_author_count=explicit_count,
            inherited_author_count=inherited_count,
            unresolved_author_count=unresolved_count,
            unique_fingerprints_count=unique_fingerprints_count,
            duplicate_fingerprint_groups_count=duplicate_groups_count,
            messages=messages,
        )

        logger.info(
            f"메시지 파싱 완료: {len(messages)}개 (context={context}, conv={conv_name}, "
            f"explicit={explicit_count}, inherited={inherited_count}, unresolved={unresolved_count})"
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
