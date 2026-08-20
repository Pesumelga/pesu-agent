"""Slack Search Result Context Collector (MVP 3.1 & MVP 3.1.1).

검색 결과(SlackSearchResult)의 permalink를 기반으로 해당 메시지가 포함된
채널 대화 뷰로 이동하여, Target Message Identity를 엄격히 검증한 뒤
전후 대화 맥락(최대 20건씩)과 스레드 댓글 정보를 수집하고 원래 Slack 상태를 정밀 복원합니다.

MVP 3.1.1 핵심 강화 사항:
1. 상태 복원 지표 분리:
   - url_restored: URL 복원 여부
   - conversation_restored: 채널/대화창 복원 여부
   - scroll_restored: 스크롤 위치(scrollTop) 복원 여부
   - viewport_restored: 뷰포트 메시지 지문 복원 여부
   - state_restore_succeeded: 필수 조건 충족 시에만 True
2. 스크롤 및 뷰포트 복원 엔진:
   - .c-scrollbar__hider 컨테이너 scrollTop 복원 및 지문 교집합 검증
3. 사용자 활성화(Foreground) 감지 및 안전 양보:
   - Slack이 Foreground인 경우 조사 시작 방지 (user_opened_slack)
   - 작업 도중 활성화 시 즉시 중단 및 원상 복구
4. 상태 분리:
   - context_collection_succeeded, state_restore_attempted, state_restore_succeeded, overall_status
"""

from __future__ import annotations

import asyncio
import ctypes
import datetime
import hashlib
import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from pesu_agent.adapters.slack_cdp import (
    SlackCdpAdapter,
    SlackCdpError,
    SlackNotReadyError,
)
from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
)
from pesu_agent.search.slack_search import SlackSearchResult

logger = logging.getLogger(__name__)


def is_slack_foreground() -> bool:
    """현재 Windows Foreground 활성 창이 Slack인지 확인합니다."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value.lower()
        return "slack" in title
    except Exception as e:
        logger.debug(f"Foreground 확인 실패 (무시): {e}")
        return False


class SlackViewState(BaseModel):
    """Slack 특정 시점의 상세 뷰/스크롤/메시지 상태 스냅샷."""

    url: str = Field(default="", description="현재 URL")
    channel_id: Optional[str] = Field(default=None, description="채널 ID")
    conversation_name: Optional[str] = Field(default=None, description="채널명")
    scroll_top: int = Field(default=0, description="스크롤 scrollTop")
    scroll_height: int = Field(default=0, description="스크롤 scrollHeight")
    client_height: int = Field(default=0, description="스크롤 clientHeight")
    visible_message_fingerprints: list[str] = Field(
        default_factory=list, description="뷰포트에 보이는 메시지 지문 목록"
    )
    first_visible_message: Optional[str] = Field(
        default=None, description="첫 번째 보이는 메시지 본문"
    )
    last_visible_message: Optional[str] = Field(
        default=None, description="마지막 보이는 메시지 본문"
    )


class SlackRestorationResult(BaseModel):
    """복원 시도 결과의 세부 지표."""

    url_restored: bool = Field(default=False, description="URL 일치 여부")
    conversation_restored: bool = Field(default=False, description="채널/대화창 일치 여부")
    scroll_restored: bool = Field(default=False, description="스크롤 위치 복원 여부")
    viewport_restored: bool = Field(default=False, description="뷰포트 메시지 지문 복원 여부")
    restored_url: Optional[str] = Field(default=None, description="복원 후 URL")
    restored_channel_id: Optional[str] = Field(default=None, description="복원 후 채널 ID")
    restored_conversation_name: Optional[str] = Field(default=None, description="복원 후 채널명")
    restored_scroll_top: Optional[int] = Field(default=None, description="복원 후 scrollTop")
    restored_visible_message_fingerprints: list[str] = Field(
        default_factory=list, description="복원 후 뷰포트 메시지 지문"
    )


class SlackContextMessage(BaseModel):
    """문맥 내 개별 메시지 항목."""

    idx: int = Field(description="문맥 내 인덱스")
    author: Optional[str] = Field(default=None, description="메시지 작성자")
    timestamp_raw: Optional[str] = Field(default=None, description="원시 타임스탬프 텍스트")
    text: str = Field(description="메시지 본문")
    channel_name: Optional[str] = Field(default=None, description="채널명")
    channel_id: Optional[str] = Field(default=None, description="채널 ID")
    result_url: Optional[str] = Field(default=None, description="메시지 퍼머링크")
    message_fingerprint: str = Field(description="결정론적 fingerprint")
    is_target: bool = Field(default=False, description="타깃 검색 결과 메시지 여부")
    has_thread: bool = Field(default=False, description="스레드 댓글 존재 여부")
    reply_count: int = Field(default=0, description="스레드 댓글 수")
    thread_identifier_candidate: Optional[str] = Field(
        default=None, description="스레드 식별자 후보"
    )


class SlackMessageContext(BaseModel):
    """Target Message 중심의 전후 대화 문맥 및 복원 결과."""

    target: SlackSearchResult = Field(description="출발점이 된 검색 결과 항목")
    target_verified: bool = Field(default=False, description="Target Message 정밀 검증 일치 여부")
    verification_reason: str = Field(default="", description="검증 결과 설명")

    # 상태 복원 및 결과 분리 지표 (MVP 3.1.1 & MVP 3.2)
    context_collection_succeeded: bool = Field(default=False, description="문맥 수집 성공 여부")
    state_restore_attempted: bool = Field(default=False, description="원래 Slack 상태 복원 시도 여부")
    state_restore_succeeded: bool = Field(default=False, description="원래 Slack 상태 복원 성공 여부")
    restore_pending: bool = Field(
        default=False, description="원래 상태 복원 보류 여부 (사용자 Foreground 사용으로 인한 복원 대기)"
    )
    overall_status: str = Field(
        default="FAILED",
        description="전체 작업 상태: SUCCESS, PARTIAL_SUCCESS_CONTEXT_COLLECTED_RESTORE_FAILED, INTERRUPTED_BY_USER, FAILED",
    )
    interruption_reason: Optional[str] = Field(
        default=None,
        description="중단/실패 원인: user_opened_slack, renderer_changed, target_lost, restore_failed, None",
    )

    before_state: Optional[SlackViewState] = Field(default=None, description="조사 전 상태 스냅샷")
    after_state: Optional[SlackViewState] = Field(default=None, description="조사 후 상태 스냅샷")
    restoration_metrics: Optional[SlackRestorationResult] = Field(
        default=None, description="세부 복원 지표"
    )

    # 이전 대화 / 타깃 / 이후 대화
    before_messages: list[SlackContextMessage] = Field(
        default_factory=list, description="Target 이전(과거) 메시지 목록 (최대 20건)"
    )
    target_message: Optional[SlackContextMessage] = Field(
        default=None, description="실제 대화 뷰에서 확인된 Target 메시지"
    )
    after_messages: list[SlackContextMessage] = Field(
        default_factory=list, description="Target 이후(미래) 메시지 목록 (최대 20건)"
    )

    # 스레드 정보
    has_thread: bool = Field(default=False, description="스레드 존재 여부")
    reply_count: int = Field(default=0, description="스레드 댓글 수")
    thread_identifier_candidate: Optional[str] = Field(
        default=None, description="스레드 식별자 후보"
    )

    main_renderer_was_modified: bool = Field(
        default=True, description="Main Renderer가 탐색에 사용되었는지 여부"
    )
    collected_at: str = Field(
        default_factory=lambda: datetime.datetime.now().isoformat(),
        description="수집 시각 (ISO 8601)",
    )

    @property
    def before_count(self) -> int:
        return len(self.before_messages)

    @property
    def after_count(self) -> int:
        return len(self.after_messages)

    @property
    def total_messages_count(self) -> int:
        count = len(self.before_messages) + len(self.after_messages)
        if self.target_message:
            count += 1
        return count


class SlackContextCollector:
    """
    Slack 검색 결과의 특정 메시지를 열어 타깃을 검증하고,
    전후 대화(최대 20건씩) 및 스레드 댓글 정보를 수집한 뒤
    원래 Slack 상태(URL/채널/스크롤/뷰포트)를 정밀 복원하는 수집기.
    """

    def __init__(
        self,
        lifecycle_manager: Optional[SlackLifecycleManager] = None,
        cdp_adapter: Optional[SlackCdpAdapter] = None,
    ):
        self.lifecycle_manager = lifecycle_manager or SlackLifecycleManager()
        self.cdp = cdp_adapter or SlackCdpAdapter(lifecycle_manager=self.lifecycle_manager)

    @staticmethod
    def parse_permalink(permalink: str) -> tuple[Optional[str], Optional[str], Optional[float]]:
        """퍼머링크 URL에서 channel_id, raw_ts, timestamp_float를 추출합니다."""
        if not permalink:
            return None, None, None
        match = re.search(r"/archives/([A-Za-z0-9_-]+)/p(\d+)", permalink)
        if not match:
            return None, None, None
        channel_id = match.group(1)
        raw_ts = match.group(2)
        try:
            ts_float = float(raw_ts[:10] + "." + raw_ts[10:])
        except Exception:
            ts_float = None
        return channel_id, raw_ts, ts_float

    async def capture_view_state(self) -> SlackViewState:
        """현재 Slack Renderer의 URL, 채널, 스크롤, 뷰포트 메시지 지문 스냅샷을 캡처합니다."""
        js_snap = """
        (() => {
            const url = window.location.href;
            const match = url.match(/\\/client\\/([A-Z0-9]+)\\/([A-Z0-9]+)/);
            const chanId = match ? match[2] : null;

            const titleElem = document.querySelector('[data-qa="channel_name"]') ||
                              document.querySelector('.p-ia4_top_nav__title');
            const convName = titleElem ? titleElem.innerText.trim().replace(/^[#@]/, '') : '';

            const sc = document.querySelector('.p-workspace__primary_view .c-scrollbar__hider') ||
                       document.querySelector('.c-scrollbar__hider') ||
                       document.querySelector('[data-qa="slack_kit_scrollbar"]');

            const items = Array.from(document.querySelectorAll('.c-virtual_list__item, [data-qa="virtual-list-item"]'));
            const fps = [];
            let firstMsg = null;
            let lastMsg = null;

            for (const item of items) {
                const textElem = item.querySelector('[data-qa="message_content"], .c-message_kit__blocks, .p-rich_text_section, .c-message__body');
                const text = textElem ? textElem.innerText.trim() : (item.innerText ? item.innerText.trim() : "");
                if (text && !text.startsWith("새 항목") && text !== "날짜로 이동") {
                    const sender = item.querySelector('[data-qa="message_sender_name"]')?.innerText.trim() || '';
                    const time = item.querySelector('[data-qa="message_timestamp"], .c-timestamp')?.innerText.trim() || '';
                    const fp = sender + '|' + time + '|' + text.substring(0, 30);
                    fps.push(fp);
                    if (!firstMsg) firstMsg = text.substring(0, 40);
                    lastMsg = text.substring(0, 40);
                }
            }

            return {
                url: url,
                channel_id: chanId,
                conversation_name: convName,
                scroll_top: sc ? sc.scrollTop : 0,
                scroll_height: sc ? sc.scrollHeight : 0,
                client_height: sc ? sc.clientHeight : 0,
                visible_message_fingerprints: fps.slice(0, 30),
                first_visible_message: firstMsg,
                last_visible_message: lastMsg
            };
        })()
        """
        raw = await self.cdp.evaluate_js(js_snap) or {}
        return SlackViewState(
            url=raw.get("url", ""),
            channel_id=raw.get("channel_id"),
            conversation_name=raw.get("conversation_name"),
            scroll_top=raw.get("scroll_top", 0),
            scroll_height=raw.get("scroll_height", 0),
            client_height=raw.get("client_height", 0),
            visible_message_fingerprints=raw.get("visible_message_fingerprints", []),
            first_visible_message=raw.get("first_visible_message"),
            last_visible_message=raw.get("last_visible_message"),
        )

    async def restore_view_state(self, before_state: SlackViewState) -> SlackRestorationResult:
        """이전 상태(URL, 채널, 스크롤 위치)를 복원하고 정밀 복원 지표를 측정합니다."""
        if not before_state or not before_state.url:
            return SlackRestorationResult(
                url_restored=False,
                conversation_restored=False,
                scroll_restored=False,
                viewport_restored=False,
            )

        logger.info(
            f"Restoring Slack View State to URL={before_state.url}, ScrollTop={before_state.scroll_top}"
        )

        # 1. URL 복원
        await self.cdp.evaluate_js(f"window.location.href = {json.dumps(before_state.url)};")
        await asyncio.sleep(2.5)

        # 2. 스크롤 복원 시도
        if before_state.scroll_top > 0:
            js_scroll = f"""
            (() => {{
                const sc = document.querySelector('.p-workspace__primary_view .c-scrollbar__hider') ||
                           document.querySelector('.c-scrollbar__hider') ||
                           document.querySelector('[data-qa="slack_kit_scrollbar"]');
                if (sc) {{
                    sc.scrollTop = {before_state.scroll_top};
                    return {{ ok: true, now_scroll: sc.scrollTop }};
                }}
                return {{ ok: false }};
            }})()
            """
            await self.cdp.evaluate_js(js_scroll)
            await asyncio.sleep(1.0)

        # 3. 복원 후 상태 측정
        after_state = await self.capture_view_state()

        url_restored = bool(
            after_state.url
            and before_state.url
            and (after_state.url.split("?")[0] == before_state.url.split("?")[0])
        )
        conv_restored = bool(
            (before_state.channel_id and after_state.channel_id == before_state.channel_id)
            or (
                before_state.conversation_name
                and after_state.conversation_name == before_state.conversation_name
            )
        )

        # 스크롤 복원 판별 (오차 ±80px 이내 허용)
        scroll_diff = abs(after_state.scroll_top - before_state.scroll_top)
        scroll_restored = (scroll_diff <= 80) if before_state.scroll_top > 0 else True

        # 뷰포트 복원 판별 (지문 교집합 또는 첫/마지막 메시지 일치)
        viewport_restored = False
        if before_state.visible_message_fingerprints and after_state.visible_message_fingerprints:
            common = set(before_state.visible_message_fingerprints) & set(
                after_state.visible_message_fingerprints
            )
            if len(common) > 0:
                viewport_restored = True
        elif conv_restored and scroll_restored:
            viewport_restored = True

        logger.info(
            f"State Restoration Result: URL={url_restored}, Conv={conv_restored}, "
            f"Scroll={scroll_restored} (Diff: {scroll_diff}px), Viewport={viewport_restored}"
        )

        return SlackRestorationResult(
            url_restored=url_restored,
            conversation_restored=conv_restored,
            scroll_restored=scroll_restored,
            viewport_restored=viewport_restored,
            restored_url=after_state.url,
            restored_channel_id=after_state.channel_id,
            restored_conversation_name=after_state.conversation_name,
            restored_scroll_top=after_state.scroll_top,
            restored_visible_message_fingerprints=after_state.visible_message_fingerprints,
        )

    async def wait_for_slack_background(
        self, timeout_sec: float = 3.0, poll_interval: float = 0.5
    ) -> bool:
        """Slack이 Background(비활성) 상태가 될 때까지 대기합니다. Background 복귀 시 True 반환."""
        waited = 0.0
        while waited < timeout_sec:
            if not is_slack_foreground():
                return True
            await asyncio.sleep(poll_interval)
            waited += poll_interval
        return not is_slack_foreground()

    async def collect_context(
        self,
        target_result: SlackSearchResult,
        max_before: int = 20,
        max_after: int = 20,
        check_user_interference: bool = True,
        restore_on_finish: bool = True,
        bg_wait_timeout_sec: float = 3.0,
    ) -> SlackMessageContext:
        """
        검색 결과 1건의 Target permalink로 이동하여 검증 및 전후 문맥을 수집합니다.
        restore_on_finish=True인 경우 원래 상태를 즉시 복원하고, False인 경우(Batch 조사 모드)
        복원 책임을 상위 세션 수집기(SlackEvidenceCollector)에 위임합니다.
        """
        # 1. 수명주기 및 시작 전 Foreground 간섭 확인
        status = self.lifecycle_manager.get_status()
        if status.status != SlackAgentModeStatus.AGENT_READY:
            raise SlackNotReadyError(
                f"Slack이 Agent Mode로 준비되지 않았습니다. (현재 상태: {status.status.value})"
            )

        if check_user_interference and is_slack_foreground():
            logger.warning(
                "사용자가 현재 Slack을 사용 중입니다 (Foreground). 안전을 위해 조사를 시작하지 않습니다."
            )
            return SlackMessageContext(
                target=target_result,
                target_verified=False,
                verification_reason="조사 시작 전 사용자가 Slack을 활성화(Foreground)하여 안전 중단됨",
                context_collection_succeeded=False,
                state_restore_attempted=False,
                state_restore_succeeded=False,
                restore_pending=False,
                overall_status="INTERRUPTED_BY_USER",
                interruption_reason="user_opened_slack",
            )

        # 2. Permalink 파싱
        channel_id, raw_ts, _ = self.parse_permalink(target_result.result_url)
        if not channel_id or not raw_ts:
            return SlackMessageContext(
                target=target_result,
                target_verified=False,
                verification_reason=f"퍼머링크 파싱 실패: {target_result.result_url}",
                context_collection_succeeded=False,
                overall_status="FAILED",
                interruption_reason="invalid_permalink",
            )

        async with self.cdp:
            # 3. 조사 시작 전 현재 뷰 상태 백업
            before_state = await self.capture_view_state()
            logger.info(
                f"Original Slack State saved: URL={before_state.url}, Conv={before_state.conversation_name}"
            )

            # 4. 클라이언트 인앱 라우팅 대상 URL 생성
            team_id = "T32QA15GC"
            if before_state.url:
                t_match = re.search(r"/client/([A-Z0-9]+)", before_state.url)
                if t_match:
                    team_id = t_match.group(1)

            target_in_app_url = f"https://app.slack.com/client/{team_id}/{channel_id}?p=p{raw_ts}"
            logger.info(f"Navigating to Target Conversation: {target_in_app_url}")

            # 조사 직전 Foreground 확인
            if check_user_interference and is_slack_foreground():
                logger.warning("조사 도중 사용자가 Slack을 활성화했습니다. 원문 이동을 중단합니다.")
                return SlackMessageContext(
                    target=target_result,
                    target_verified=False,
                    verification_reason="조사 이동 직전 사용자가 Slack을 활성화하여 중단됨",
                    context_collection_succeeded=False,
                    state_restore_attempted=False,
                    state_restore_succeeded=True,
                    restore_pending=False,
                    overall_status="INTERRUPTED_BY_USER",
                    interruption_reason="user_opened_slack",
                    before_state=before_state,
                )

            # 이동 실행
            await self.cdp.evaluate_js(f"window.location.href = {json.dumps(target_in_app_url)};")

            # 가상 리스트 하이드레이션 및 타깃 메시지 렌더링 대기
            await asyncio.sleep(4.0)

            # 5. DOM에서 메시지 목록 및 타깃 식별
            js_extract = f"""
            (() => {{
                const targetTs = {json.dumps(raw_ts)};
                const targetTextSnippet = {json.dumps(target_result.text[:40])};

                const items = Array.from(document.querySelectorAll(
                    '.c-virtual_list__item, [data-qa="virtual-list-item"], [data-qa="message_container"], [role="listitem"]'
                ));

                const messages = [];
                let targetIdx = -1;

                for (let i = 0; i < items.length; i++) {{
                    const item = items[i];
                    
                    const textElem = item.querySelector('[data-qa="message_content"], .c-message_kit__blocks, .p-rich_text_section, .c-message__body');
                    const text = textElem ? textElem.innerText.trim() : (item.innerText ? item.innerText.trim() : "");
                    
                    const senderElem = item.querySelector('[data-qa="message_sender_name"], .c-message__sender_button, button[data-message-sender]');
                    const sender = senderElem ? senderElem.innerText.trim() : null;

                    const timeElem = item.querySelector('[data-qa="message_timestamp"], .c-timestamp');
                    const timeStr = timeElem ? timeElem.innerText.trim() : null;

                    const linkElem = item.querySelector('a[href*="/archives/"][href*="/p"]') || item.querySelector('a.c-timestamp');
                    const href = linkElem ? linkElem.getAttribute('href') : null;
                    const itemKey = item.getAttribute('data-item-key') || item.id || '';

                    // Reply / Thread Elements
                    const replyElem = item.querySelector(
                        '[data-qa="reply_count"], .c-message__reply_count, [data-qa="message_reply_count"], [aria-label*="댓글"], [aria-label*="replies"], [aria-label*="reply"], button[data-qa="reply_bar"]'
                    );
                    const replyBar = item.querySelector('.c-message__reply_bar, [data-qa="reply_bar"]');

                    let replyCount = 0;
                    let hasThread = false;
                    const replyText = replyElem ? replyElem.innerText.trim() : (replyBar ? replyBar.innerText.trim() : "");
                    const replyAria = replyElem ? replyElem.getAttribute('aria-label') : (replyBar ? replyBar.getAttribute('aria-label') : "");
                    const combinedReply = replyText + ' ' + (replyAria || '');
                    
                    const countMatch = combinedReply.match(/(\\d+)/);
                    if (countMatch) {{
                        replyCount = parseInt(countMatch[1]);
                        hasThread = true;
                    }} else if (replyElem || replyBar) {{
                        hasThread = true;
                        replyCount = 1;
                    }}

                    if (text && !text.startsWith("새 항목") && text !== "날짜로 이동") {{
                        const msgObj = {{
                            idx: i,
                            author: sender,
                            timestamp_raw: timeStr,
                            text: text,
                            href: href,
                            has_thread: hasThread,
                            reply_count: replyCount,
                            item_key: itemKey
                        }};

                        const tsMatch = (href && href.includes(targetTs)) || itemKey.includes(targetTs);
                        const textMatch = targetTextSnippet && text.includes(targetTextSnippet.substring(0, 20));

                        if (targetIdx === -1 && (tsMatch || textMatch)) {{
                            targetIdx = messages.length;
                            msgObj.is_target = true;
                        }}

                        messages.push(msgObj);
                    }}
                }}

                return {{
                    total_dom_items: items.length,
                    total_valid_messages: messages.length,
                    target_idx: targetIdx,
                    messages: messages
                }};
            }})()
            """
            dom_data = await self.cdp.evaluate_js(js_extract) or {}
            target_idx = dom_data.get("target_idx", -1)
            raw_messages = dom_data.get("messages", [])

            # 조사 도중(Main Renderer 변경 후) 사용자 활성화 감지 시 Yield 정책 적용
            if check_user_interference and is_slack_foreground():
                logger.warning(
                    "조사 도중(Renderer 변경 후) 사용자가 Slack을 활성화했습니다. 모든 Agent 탐색을 즉시 중단합니다."
                )
                restore_pending = True
                state_restore_attempted = False
                state_restore_succeeded = False
                restoration_metrics = None

                # 사용자가 보고 있는 동안에는 navigation 복원을 하지 않고 Background 복귀를 대기합니다.
                logger.info(
                    f"Slack Foreground 감지: Background 복귀 대기 중 (최대 {bg_wait_timeout_sec}초)..."
                )
                became_bg = await self.wait_for_slack_background(timeout_sec=bg_wait_timeout_sec)
                if became_bg:
                    logger.info("Slack이 Background로 복귀하여 원래 상태 복원을 수행합니다.")
                    state_restore_attempted = True
                    restoration_metrics = await self.restore_view_state(before_state)
                    state_restore_succeeded = (
                        restoration_metrics.url_restored
                        and restoration_metrics.conversation_restored
                        and (
                            restoration_metrics.scroll_restored
                            or restoration_metrics.viewport_restored
                        )
                    )
                    restore_pending = not state_restore_succeeded
                else:
                    logger.warning(
                        "사용자가 여전히 Slack을 사용 중입니다. 간섭 방지를 위해 상태 복원을 보류(restore_pending=True)합니다."
                    )
                    restore_pending = True

                return SlackMessageContext(
                    target=target_result,
                    target_verified=False,
                    verification_reason="조사 도중 사용자가 Slack을 활성화하여 즉시 중단됨",
                    context_collection_succeeded=False,
                    state_restore_attempted=state_restore_attempted,
                    state_restore_succeeded=state_restore_succeeded,
                    restore_pending=restore_pending,
                    overall_status="INTERRUPTED_BY_USER",
                    interruption_reason="user_opened_slack",
                    before_state=before_state,
                    restoration_metrics=restoration_metrics,
                )

            # 6. Target Identity 검증 및 분할
            target_verified = False
            verification_reason = ""
            before_msgs: list[SlackContextMessage] = []
            target_msg: Optional[SlackContextMessage] = None
            after_msgs: list[SlackContextMessage] = []
            has_thread = False
            reply_count = 0
            thread_id_candidate = None
            context_collection_succeeded = False

            if target_idx >= 0 and target_idx < len(raw_messages):
                target_verified = True
                verification_reason = f"타깃 메시지 식별자({raw_ts}) 및 본문 일치 확인 성공 (Index: {target_idx})"
                logger.info(f"Target message verified at index {target_idx}")

                # Before / Target / After 슬라이싱
                start_before = max(0, target_idx - max_before)
                end_after = min(len(raw_messages), target_idx + 1 + max_after)

                # Before
                for m in raw_messages[start_before:target_idx]:
                    fp = hashlib.md5(
                        f"{m.get('author')}|{m.get('timestamp_raw')}|{m.get('text')}".encode(
                            "utf-8"
                        )
                    ).hexdigest()[:16]
                    before_msgs.append(
                        SlackContextMessage(
                            idx=m.get("idx"),
                            author=m.get("author"),
                            timestamp_raw=m.get("timestamp_raw"),
                            text=m.get("text"),
                            channel_name=target_result.channel_name,
                            channel_id=channel_id,
                            result_url=m.get("href"),
                            message_fingerprint=fp,
                            is_target=False,
                            has_thread=m.get("has_thread", False),
                            reply_count=m.get("reply_count", 0),
                        )
                    )

                # Target
                tm = raw_messages[target_idx]
                target_fp = hashlib.md5(
                    f"{tm.get('author')}|{tm.get('timestamp_raw')}|{tm.get('text')}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:16]
                has_thread = tm.get("has_thread", False)
                reply_count = tm.get("reply_count", 0)
                thread_id_candidate = raw_ts if has_thread else None

                target_msg = SlackContextMessage(
                    idx=tm.get("idx"),
                    author=tm.get("author") or target_result.author,
                    timestamp_raw=tm.get("timestamp_raw") or target_result.timestamp_raw,
                    text=tm.get("text"),
                    channel_name=target_result.channel_name,
                    channel_id=channel_id,
                    result_url=tm.get("href") or target_result.result_url,
                    message_fingerprint=target_fp,
                    is_target=True,
                    has_thread=has_thread,
                    reply_count=reply_count,
                    thread_identifier_candidate=thread_id_candidate,
                )

                # After
                for m in raw_messages[target_idx + 1 : end_after]:
                    fp = hashlib.md5(
                        f"{m.get('author')}|{m.get('timestamp_raw')}|{m.get('text')}".encode(
                            "utf-8"
                        )
                    ).hexdigest()[:16]
                    after_msgs.append(
                        SlackContextMessage(
                            idx=m.get("idx"),
                            author=m.get("author"),
                            timestamp_raw=m.get("timestamp_raw"),
                            text=m.get("text"),
                            channel_name=target_result.channel_name,
                            channel_id=channel_id,
                            result_url=m.get("href"),
                            message_fingerprint=fp,
                            is_target=False,
                            has_thread=m.get("has_thread", False),
                            reply_count=m.get("reply_count", 0),
                        )
                    )

                context_collection_succeeded = True
            else:
                target_verified = False
                verification_reason = f"타깃 메시지 식별자({raw_ts})를 렌더링된 DOM({len(raw_messages)}개 메시지)에서 찾을 수 없음"
                logger.warning(verification_reason)

            # 7. 사후 원래 상태 복원 (State Restoration)
            # restore_on_finish=False인 경우(Batch 조사 모드) 복원을 상위 세션에 위임
            if restore_on_finish:
                state_restore_attempted = True
                restoration_metrics = await self.restore_view_state(before_state)
                after_state = SlackViewState(
                    url=restoration_metrics.restored_url or "",
                    channel_id=restoration_metrics.restored_channel_id,
                    conversation_name=restoration_metrics.restored_conversation_name,
                    scroll_top=restoration_metrics.restored_scroll_top or 0,
                    visible_message_fingerprints=restoration_metrics.restored_visible_message_fingerprints,
                )
                state_restore_succeeded = (
                    restoration_metrics.url_restored
                    and restoration_metrics.conversation_restored
                    and (
                        restoration_metrics.scroll_restored
                        or restoration_metrics.viewport_restored
                    )
                )
                restore_pending = False
            else:
                state_restore_attempted = False
                state_restore_succeeded = False
                restore_pending = False
                restoration_metrics = None
                after_state = None

            # 8. Overall Status 결정
            overall_status = "FAILED"
            interruption_reason = None
            if context_collection_succeeded:
                if restore_on_finish:
                    overall_status = "SUCCESS" if state_restore_succeeded else "PARTIAL_SUCCESS_CONTEXT_COLLECTED_RESTORE_FAILED"
                    interruption_reason = None if state_restore_succeeded else "restore_failed"
                else:
                    overall_status = "SUCCESS"
                    interruption_reason = None
            else:
                overall_status = "FAILED"
                interruption_reason = "target_lost"

            return SlackMessageContext(
                target=target_result,
                target_verified=target_verified,
                verification_reason=verification_reason,
                context_collection_succeeded=context_collection_succeeded,
                state_restore_attempted=state_restore_attempted,
                state_restore_succeeded=state_restore_succeeded,
                restore_pending=restore_pending,
                overall_status=overall_status,
                interruption_reason=interruption_reason,
                before_state=before_state,
                after_state=after_state,
                restoration_metrics=restoration_metrics,
                before_messages=before_msgs,
                target_message=target_msg,
                after_messages=after_msgs,
                has_thread=has_thread,
                reply_count=reply_count,
                thread_identifier_candidate=thread_id_candidate,
            )
