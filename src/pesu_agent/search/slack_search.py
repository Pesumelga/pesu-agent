"""Slack Background Search Engine with Freshness & Correctness Validation (MVP 3.0.1).

사용자가 다른 프로그램(Excel, Chrome, IDE 등)을 사용하는 동안,
Slack 데스크톱 앱의 UI/마우스/키보드에 일절 간섭하지 않고(0% 간섭)
CDP/DOM을 통해 검색어를 안전하게 입력하고, 쿼리 반영 여부(observed_query)와
이전 검색 결과 잔존 여부(result_signature, freshness guard)를 엄격히 검증하여
신뢰성 있는 구조화된 검색 결과를 수집합니다.

보안 및 안전 제약:
- 검색창 DOM 값 변경, Enter 실행, 결과 영역 스크롤 및 읽기만 수행
- 검색 결과 클릭, 채널 이동, DM 이동, 스레드 열기, 메시지 전송, 데이터 수정 일절 금지
- 사전 상태가 AGENT_READY가 아니면 검색을 시작하지 않음
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import re
import uuid
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

logger = logging.getLogger(__name__)


class SlackSearchStaleError(SlackCdpError):
    """이전 검색 결과가 갱신되지 않고 남아있을 때 발생하는 예외."""


class SlackSearchResult(BaseModel):
    """단일 Slack 검색 결과 항목."""

    result_index: int = Field(description="검색 결과 내 인덱스 (1-based)")
    query: str = Field(description="검색어")
    author: Optional[str] = Field(default=None, description="메시지 작성자")
    timestamp_raw: Optional[str] = Field(default=None, description="원시 타임스탬프 텍스트")
    text: str = Field(description="메시지 본문 요약 또는 전문")
    channel_name: Optional[str] = Field(default=None, description="메시지가 속한 실제 채널/DM명 (추측 불가 시 null)")
    channel_id: Optional[str] = Field(default=None, description="채널 ID (예: C3R53LYV8)")
    context: str = Field(default="search_result", description="컨텍스트 구분 (search_result)")
    mentions: list[str] = Field(default_factory=list, description="멘션(@) 목록")
    links: list[str] = Field(default_factory=list, description="포함된 URL 링크 목록")
    uia_message_id: Optional[str] = Field(default=None, description="UIA/DOM 메시지 고유 식별자 후보")
    result_url: Optional[str] = Field(default=None, description="검색 결과 퍼머링크 (archives URL)")
    message_fingerprint: str = Field(description="중복 제거용 로컬 세션 해시")


class SlackSearchSession(BaseModel):
    """Slack 검색 세션 전체 결과 (Freshness Guard 적용)."""

    search_session_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="검색 세션 고유 식별자 (UUID4)",
    )
    searched_at: str = Field(description="검색 수행 시각 (ISO 8601)")
    requested_query: str = Field(description="사용자가 요청한 검색어")
    observed_query: str = Field(description="Slack 검색 UI에 실제 반영되어 관측된 검색어")
    query_verified: bool = Field(description="requested_query == observed_query 검증 성공 여부")
    result_freshness_verified: bool = Field(
        description="이전 검색 결과와의 signature 비교를 통한 최신성 검증 성공 여부"
    )
    result_signature: str = Field(description="검색 결과 뷰포트 상태 해시 서명")
    query_literal_match_count: int = Field(
        default=0, description="결과 텍스트 내 검색어 리터럴 포함 건수"
    )
    stale_result_suspected: bool = Field(
        default=False, description="이전 쿼리 결과 잔존 의심 여부"
    )
    result_count: int = Field(description="수집된 전체 검색 결과 수 (하위 호환용)")
    collected_result_count: int = Field(
        default=0, description="현재 뷰포트/스크롤에서 실제 수집된 결과 개수"
    )
    unique_collected_result_count: int = Field(
        default=0, description="중복 제거된 고유 수집 결과 개수"
    )
    total_result_count_reported_by_slack: Optional[int] = Field(
        default=None, description="Slack UI에서 공식 보고된 전체 검색 결과 총수 (미확인 시 null)"
    )
    collection_complete: bool = Field(
        default=False, description="전체 검색 결과가 100% 빠짐없이 수집 완료되었는지 여부"
    )
    unique_result_count: int = Field(description="중복 제거된 고유 검색 결과 수")
    has_more_results: bool = Field(default=False, description="추가 검색 결과 존재 여부")
    search_scope: str = Field(default="default", description="검색 범위 (default)")
    scroll_iterations: int = Field(default=0, description="수행한 백그라운드 스크롤 횟수")
    results: list[SlackSearchResult] = Field(default_factory=list, description="검색 결과 목록")


class SlackSearch:
    """CDP 기반 백그라운드 Slack 검색 실행기 (Freshness Guard 포함)."""

    def __init__(
        self,
        cdp_adapter: Optional[SlackCdpAdapter] = None,
        lifecycle_manager: Optional[SlackLifecycleManager] = None,
        cdp_port: int = 9222,
    ):
        self.lifecycle = lifecycle_manager or SlackLifecycleManager(cdp_port=cdp_port)
        self.cdp = cdp_adapter or SlackCdpAdapter(port=cdp_port, lifecycle_manager=self.lifecycle)
        self._last_result_signature: Optional[str] = None

    @staticmethod
    def compute_fingerprint(
        channel_name: Optional[str],
        timestamp_raw: Optional[str],
        text: str,
        result_url: Optional[str],
    ) -> str:
        """검색 결과의 불변 속성을 이용한 결정론적 fingerprint 계산."""
        norm_text = re.sub(r"\s+", " ", text.strip())
        raw_key = f"{channel_name or ''}|{timestamp_raw or ''}|{norm_text}|{result_url or ''}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def compute_result_signature(query: str, results: list[SlackSearchResult]) -> str:
        """현재 검색 결과 집합의 첫 N개 항목을 기반으로 상태 서명(Signature)을 생성합니다."""
        if not results:
            return hashlib.sha256(f"empty:{query}".encode("utf-8")).hexdigest()[:16]
        items_summary = "|".join(
            [f"{r.channel_name}:{r.result_url}:{r.text[:30]}" for r in results[:5]]
        )
        raw_key = f"{query}|{items_summary}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def extract_mentions_and_links(text: str) -> tuple[list[str], list[str]]:
        """텍스트 내 멘션(@) 및 URL 링크 추출."""
        mentions = list(set(re.findall(r"@[\w\uac00-\ud7a3\.\-]+", text)))
        links = list(set(re.findall(r"https?://[^\s<>\"']+", text)))
        return mentions, links

    async def open_search_ui(self, timeout_sec: float = 8.0) -> bool:
        """방어적 다중 셀렉터, CDP 가상 마우스 클릭 및 폴링으로 Slack 전역 검색창을 활성화합니다."""
        js_find_rect = """
        (() => {
            // 1. 이미 입력창이 존재하는지 확인
            const input = document.querySelector('input[data-qa="top_nav_search__input"]') ||
                          document.querySelector('input[data-qa="search_input"]') ||
                          document.querySelector('.p-top_nav__search input') ||
                          document.querySelector('[data-qa="search_input_box"]') ||
                          document.querySelector('[data-qa="focusable_search_input"]') ||
                          document.querySelector('.c-search__input_box .ql-editor');
            if (input) {
                return { ready: true, via: 'existing_input' };
            }

            // 2. 검색 버튼이 로드되었으면 Bounding Rect 반환 및 클릭 시도
            const btn = document.querySelector('[data-qa="top_nav_search"]') ||
                        document.querySelector('button[aria-label*="검색"]') ||
                        document.querySelector('button[aria-label*="Search"]') ||
                        document.querySelector('.p-top_nav__search');
            if (btn) {
                const r = btn.getBoundingClientRect();
                return {
                    ready: false,
                    rect: { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) },
                    qa: btn.getAttribute('data-qa')
                };
            }

            return { ready: false };
        })()
        """
        start_t = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_t < timeout_sec:
            res = await self.cdp.evaluate_js(js_find_rect)
            if res:
                if res.get("ready") or res.get("ok"):
                    logger.info(f"검색 UI 활성화 완료: {res}")
                    return True
                if res.get("rect"):
                    # CDP 가상 마우스 클릭 디스패치 (OS 마우스 비침범)
                    rx, ry = res["rect"]["x"], res["rect"]["y"]
                    await self.cdp.dispatch_mouse_event(type_="mousePressed", x=rx, y=ry, button="left", click_count=1)
                    await self.cdp.dispatch_mouse_event(type_="mouseReleased", x=rx, y=ry, button="left", click_count=1)
                    logger.info(f"검색 버튼 CDP 가상 마우스 클릭 완료 ({rx}, {ry})")
                    await asyncio.sleep(0.5)
                    return True
            await asyncio.sleep(0.5)

        logger.warning("검색 UI 트리거 요소를 찾지 못했습니다.")
        return False

    async def enter_query_and_search(self, query: str, timeout_sec: float = 4.0) -> dict[str, Any]:
        """전역 검색창(Quill Rich Editor 또는 Input)을 대기/탐색하여 쿼리를 입력하고 Enter를 전송합니다."""
        js_enter = f"""
        (() => {{
            // 1. Quill Rich Editor (Slack 신규 UI) 탐색
            const editor = document.querySelector('[data-qa="search_input_box"] [contenteditable="true"]') ||
                           document.querySelector('[data-qa="focusable_search_input"] [contenteditable="true"]') ||
                           document.querySelector('.c-search__input_box .ql-editor') ||
                           document.querySelector('[data-qa="texty_input"]');
            if (editor) {{
                editor.focus();
                while (editor.firstChild) {{
                    editor.removeChild(editor.firstChild);
                }}
                const p = document.createElement('p');
                p.textContent = {json.dumps(query)};
                editor.appendChild(p);
                editor.dispatchEvent(new Event('input', {{ bubbles: true }}));
                editor.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{
                    ok: true,
                    type: 'quill_editor',
                    tag: editor.tagName,
                    value: editor.innerText || editor.textContent
                }};
            }}

            // 2. 표준 Input (Slack 클래식 UI) 탐색
            let input = document.querySelector('input[data-qa="top_nav_search__input"]') ||
                        document.querySelector('input[data-qa="search_input"]') ||
                        document.querySelector('.p-top_nav__search input') ||
                        document.querySelector('[data-qa="top_nav_search"] input') ||
                        document.querySelector('input.c-search_autocomplete__input') ||
                        document.querySelector('input[data-qa="query_input"]');

            if (!input) {{
                const candidates = Array.from(document.querySelectorAll('input[type="text"], input:not([type]), input[type="search"]'));
                input = candidates.find(el => {{
                    const qa = el.getAttribute('data-qa') || '';
                    const isHidden = el.getAttribute('type') === 'hidden' || el.style.display === 'none';
                    return !qa.includes('sidebar') && !qa.includes('filter') && !qa.includes('file') && !isHidden;
                }});
            }}

            if (input) {{
                input.focus();
                input.value = '';
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
                if (nativeSetter) {{
                    nativeSetter.call(input, {json.dumps(query)});
                }} else {{
                    input.value = {json.dumps(query)};
                }}
                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return {{
                    ok: true,
                    type: 'standard_input',
                    qa: input.getAttribute('data-qa'),
                    value: input.value,
                    is_active: document.activeElement === input
                }};
            }}

            return {{ ok: false, error: 'no_suitable_global_search_input' }};
        }})()
        """
        start_t = asyncio.get_event_loop().time()
        last_res = {"ok": False, "error": "no_suitable_global_search_input"}

        while asyncio.get_event_loop().time() - start_t < timeout_sec:
            res = await self.cdp.evaluate_js(js_enter)
            if res and res.get("ok"):
                last_res = res
                break
            await asyncio.sleep(0.4)

        if not last_res.get("ok"):
            logger.warning(f"DOM 검색어 입력 실패: {last_res}")
            return last_res

        # 3. CDP Input 가상 키 이벤트로 네이티브 수준의 Enter 전송
        await self.cdp.dispatch_key_event(
            type_="rawKeyDown", key="Enter", code="Enter", windows_virtual_key_code=13
        )
        await self.cdp.dispatch_key_event(
            type_="keyUp", key="Enter", code="Enter", windows_virtual_key_code=13
        )
        logger.info(f"전역 검색창에 쿼리 {query!r} 입력 및 Enter 이벤트 디스패치 완료.")
        return last_res

    async def parse_current_visible_results(self, query: str) -> list[SlackSearchResult]:
        """현재 DOM에 노출된 검색 결과 항목들을 정밀 파싱합니다 (실제 channel_name/ID 추출)."""
        js_parse = """
        (() => {
            const items = Array.from(document.querySelectorAll(
                '[data-qa="search_result_item"], [data-qa="search_result_message"], .c-search_result_item, .c-search_result_message, .c-message_kit__message'
            ));

            const parsed = [];
            items.forEach((item, idx) => {
                // 1. Channel Name & ID Extraction
                const chanLink = item.querySelector('a[href*="/archives/"]') ||
                                 item.querySelector('[data-qa="search_result_channel_name"]') ||
                                 item.querySelector('.c-search_result_message__channel') ||
                                 item.querySelector('.c-search_result_item__header_channel');
                
                let chanName = null;
                let chanId = null;

                if (chanLink) {
                    const linkText = chanLink.innerText.trim();
                    const href = chanLink.getAttribute('href') || '';
                    const match = href.match(/\\/archives\\/([A-Z0-9]+)/);
                    if (match) {
                        chanId = match[1];
                    }
                    // 링크 텍스트가 타임스탬프가 아닌 경우에만 채널명으로 채택
                    if (linkText && !linkText.includes('오전') && !linkText.includes('오후') && !linkText.includes(':')) {
                        chanName = linkText.replace(/^[#@]/, '').trim();
                    }
                }

                // 부모 그룹 헤더 탐색
                if (!chanName) {
                    const groupHeader = item.closest('.c-search_result_group')?.querySelector('.c-search_result_group__header');
                    if (groupHeader) {
                        chanName = groupHeader.innerText.trim().replace(/^[#@]/, '').trim();
                    }
                }

                // 2. Author
                const senderElem = item.querySelector('[data-qa="message_sender_name"], .c-message__sender_button, button[data-message-sender], .c-search_result_message__sender, .c-message_kit__sender');
                let sender = senderElem ? senderElem.innerText.trim() : null;
                if (sender) {
                    sender = sender.replace(/\\n앱$/, '').trim();
                }

                // 3. Timestamp
                const timeElem = item.querySelector('[data-qa="message_timestamp"], .c-timestamp, a.c-timestamp, .c-search_result_message__timestamp');
                const timeStr = timeElem ? timeElem.innerText.trim() : null;

                // 4. Body Text
                const textElem = item.querySelector('[data-qa="message_content"], .c-message_kit__blocks, .p-rich_text_section, .c-search_result_message__body');
                const text = textElem ? textElem.innerText.trim() : (item.innerText ? item.innerText.trim() : "");

                // 5. Permalink (정확한 메시지 URL)
                const permalinkElem = item.querySelector('a[href*="/archives/"][href*="/p"]') ||
                                      item.querySelector('a.c-timestamp[href*="/archives/"]') ||
                                      item.querySelector('a[href*="/archives/"]');
                const permalink = permalinkElem ? permalinkElem.getAttribute('href') : null;

                // 6. DOM Message Key
                const itemId = item.getAttribute('data-item-key') || item.id || null;

                if (text && !text.startsWith("새 항목") && text !== "날짜로 이동") {
                    parsed.push({
                        idx: idx,
                        channel_name: chanName,
                        channel_id: chanId,
                        sender: sender,
                        time: timeStr,
                        text: text.replace(/\\n/g, ' ').substring(0, 300),
                        permalink: permalink,
                        item_id: itemId
                    });
                }
            });
            return parsed;
        })()
        """
        raw_items = await self.cdp.evaluate_js(js_parse)
        results: list[SlackSearchResult] = []
        if not raw_items:
            return results

        for item in raw_items:
            text = item.get("text", "")
            chan = item.get("channel_name")
            chan_id = item.get("channel_id")
            sender = item.get("sender")
            time_str = item.get("time")
            permalink = item.get("permalink")
            item_id = item.get("item_id")

            mentions, links = self.extract_mentions_and_links(text)
            fingerprint = self.compute_fingerprint(chan, time_str, text, permalink)

            results.append(
                SlackSearchResult(
                    result_index=len(results) + 1,
                    query=query,
                    author=sender,
                    timestamp_raw=time_str,
                    text=text,
                    channel_name=chan,
                    channel_id=chan_id,
                    context="search_result",
                    mentions=mentions,
                    links=links,
                    uia_message_id=item_id,
                    result_url=permalink,
                    message_fingerprint=fingerprint,
                )
            )
        return results

    async def get_observed_query_state(self) -> tuple[str, bool]:
        """Slack 검색 UI에 현재 표시된 query 텍스트와 결과 0건 여부를 조회합니다."""
        js_observed = """
        (() => {
            const editorElem = document.querySelector('[data-qa="search_input_box"] [contenteditable="true"]') ||
                               document.querySelector('[data-qa="focusable_search_input"] [contenteditable="true"]') ||
                               document.querySelector('.c-search__input_box .ql-editor') ||
                               document.querySelector('[data-qa="texty_input"]');
            const editorVal = editorElem ? (editorElem.innerText || editorElem.textContent || '').trim() : '';

            const headerElem = document.querySelector('[data-qa="search_query_text"]') ||
                               document.querySelector('.p-search_results__query') ||
                               document.querySelector('[data-qa="search_header_title"]') ||
                               document.querySelector('.c-search__header');
            const headerText = headerElem ? headerElem.innerText.trim() : '';

            const inputElem = document.querySelector('input[data-qa="top_nav_search__input"]') ||
                              document.querySelector('input[data-qa="search_input"]') ||
                              document.querySelector('.p-top_nav__search input');
            const inputVal = inputElem ? inputElem.value.trim() : '';

            const emptyNotice = document.querySelector('[data-qa="search_no_results"], .c-search__empty_state');
            const itemsCount = document.querySelectorAll(
                '[data-qa="search_result_item"], [data-qa="search_result_message"], .c-search_result_item, .c-search_result_message, .c-message_kit__message'
            ).length;

            return {
                observed: editorVal || headerText || inputVal,
                is_empty: !!emptyNotice || itemsCount === 0,
                items_count: itemsCount
            };
        })()
        """
        res = await self.cdp.evaluate_js(js_observed)
        if not res:
            return "", False
        return res.get("observed", ""), res.get("is_empty", False)

    async def scroll_search_results(self, scroll_delta_px: int = 600) -> bool:
        """검색 결과 컨테이너를 하향 스크롤하여 추가 결과를 로드합니다."""
        js_scroll = f"""
        (() => {{
            const sc = document.querySelector('.c-search__results_list') ||
                       document.querySelector('.c-virtual_list__scroll_container') ||
                       document.querySelector('[data-qa="search_message_list"]') ||
                       document.querySelector('.p-search_results__view');
            if (sc) {{
                const before = sc.scrollTop;
                sc.scrollBy({{ top: {scroll_delta_px}, behavior: 'instant' }});
                return {{ ok: true, before: before, after: sc.scrollTop }};
            }}
            return {{ ok: false }};
        }})()
        """
        res = await self.cdp.evaluate_js(js_scroll)
        return bool(res and res.get("ok"))

    async def search(
        self,
        query: str,
        max_scrolls: int = 3,
        settle_delay_sec: float = 1.2,
        timeout_sec: float = 8.0,
    ) -> SlackSearchSession:
        """검색어를 실행하고 Freshness Guard를 거쳐 검증된 최신 검색 결과를 수집합니다."""
        session_id = str(uuid.uuid4())
        searched_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 1. 수명주기 상태 검증 (AGENT_READY 필수)
        status = self.lifecycle.get_status()
        if status.status != SlackAgentModeStatus.AGENT_READY:
            raise SlackNotReadyError(
                f"Slack이 Agent Mode로 준비되지 않았습니다. (현재 상태: {status.status.value})\n"
                f"안내: {status.message}"
            )

        async with self.cdp:
            # 2. Slack DOM 하이드레이션 대기
            js_ready = """
            (() => {
                const topNav = document.querySelector('.p-top_nav, [data-qa="top_nav_search"], .p-ia4_top_nav, button[aria-label*="검색"]');
                const sideBar = document.querySelector('.p-channel_sidebar, [data-qa="channel_sidebar"], .c-virtual_list');
                return { ready: !!(topNav || sideBar) };
            })()
            """
            for _ in range(20):
                chk = await self.cdp.evaluate_js(js_ready)
                if isinstance(chk, dict) and chk.get("ready"):
                    break
                await asyncio.sleep(0.5)

            # 3. 검색 전 기존 signature 기억
            prev_signature = self._last_result_signature

            # 4. 전역 검색 UI 열기
            await self.open_search_ui()
            await asyncio.sleep(1.5)

            # 5. 검색어 입력 및 실행
            enter_res = await self.enter_query_and_search(query, timeout_sec=5.0)
            if not enter_res.get("ok"):
                raise SlackCdpError(f"검색어 입력 실패: {json.dumps(enter_res, ensure_ascii=False)}")

            # 5. Freshness Guard Polling: 쿼리 반영 및 결과 갱신 대기
            start_t = asyncio.get_event_loop().time()
            observed_query = ""
            query_verified = False
            freshness_verified = False
            current_signature = ""
            initial_results: list[SlackSearchResult] = []

            while asyncio.get_event_loop().time() - start_t < timeout_sec:
                await asyncio.sleep(0.5)

                obs_q, is_empty = await self.get_observed_query_state()
                observed_query = obs_q
                query_verified = bool(query.strip().lower() in observed_query.strip().lower())

                initial_results = await self.parse_current_visible_results(query)
                current_signature = self.compute_result_signature(query, initial_results)

                # Freshness 조건 판단:
                # 1) query가 UI에 반영되었고,
                # 2) 결과가 0건(is_empty)이거나,
                # 3) 이전 검색 서명과 다른 새 서명이 확인된 경우
                if query_verified:
                    if is_empty or len(initial_results) == 0:
                        freshness_verified = True
                        break
                    if prev_signature is None or current_signature != prev_signature:
                        freshness_verified = True
                        break

            if not query_verified:
                logger.warning(
                    f"Query verification failed: requested={query!r}, observed={observed_query!r}"
                )

            if not freshness_verified and prev_signature is not None:
                raise SlackSearchStaleError(
                    f"검색 결과가 이전 검색 상태({prev_signature})에서 갱신되지 않았습니다 (Stale Result)."
                )

            self._last_result_signature = current_signature

            # 6. 결과 수집 및 추가 스크롤 처리
            seen_fingerprints: set[str] = set()
            collected_results: list[SlackSearchResult] = []

            for r in initial_results:
                if r.message_fingerprint not in seen_fingerprints:
                    seen_fingerprints.add(r.message_fingerprint)
                    r.result_index = len(collected_results) + 1
                    collected_results.append(r)

            scroll_count = 0
            has_more = False

            if max_scrolls > 0 and len(collected_results) > 0:
                for _ in range(max_scrolls):
                    scrolled = await self.scroll_search_results(scroll_delta_px=600)
                    if not scrolled:
                        break

                    await asyncio.sleep(settle_delay_sec)
                    scroll_count += 1

                    new_batch = await self.parse_current_visible_results(query)
                    added_in_scroll = 0
                    for r in new_batch:
                        if r.message_fingerprint not in seen_fingerprints:
                            seen_fingerprints.add(r.message_fingerprint)
                            r.result_index = len(collected_results) + 1
                            collected_results.append(r)
                            added_in_scroll += 1

                    if added_in_scroll > 0:
                        has_more = True
                    else:
                        break

            # 7. 의미적 Sanity Check: 리터럴 매칭 수 집계
            literal_match_cnt = sum(
                1 for r in collected_results if query.lower() in r.text.lower()
            )
            stale_suspected = len(collected_results) > 0 and literal_match_cnt == 0

            return SlackSearchSession(
                search_session_id=session_id,
                searched_at=searched_at,
                requested_query=query,
                observed_query=observed_query,
                query_verified=query_verified,
                result_freshness_verified=freshness_verified,
                result_signature=current_signature,
                query_literal_match_count=literal_match_cnt,
                stale_result_suspected=stale_suspected,
                result_count=len(collected_results),
                collected_result_count=len(collected_results),
                unique_collected_result_count=len(seen_fingerprints),
                total_result_count_reported_by_slack=None,
                collection_complete=not has_more and len(collected_results) > 0,
                unique_result_count=len(seen_fingerprints),
                has_more_results=has_more,
                search_scope="default",
                scroll_iterations=scroll_count,
                results=collected_results,
            )
