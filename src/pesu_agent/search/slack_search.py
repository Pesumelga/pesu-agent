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
from pesu_agent.lifecycle.slack_agent_window import (
    SlackAgentWindowManager,
    SlackAgentWindowState,
)
from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
)
from pesu_agent.security.read_only_barrier import (
    SlackSearchInputIdentityChangedError,
    SlackVerifiedSearchInputNotFoundError,
)

logger = logging.getLogger(__name__)


class SlackSearchStaleError(SlackCdpError):
    """이전 검색 결과가 갱신되지 않고 남아있을 때 발생하는 예외."""


class SlackResultReadinessTimeoutError(SlackCdpError):
    """Result readiness gate timeout: surface remained in transient LOADING/UNKNOWN state past deadline.

    This is semantically distinct from valid EMPTY_RESULTS.
    Downstream must NOT treat this as 'search returned 0 results'.
    """

    def __init__(self, failure_code: str, surface_category: str, message: str = "") -> None:
        self.failure_code = failure_code
        self.surface_category = surface_category
        super().__init__(
            message or f"Result readiness timeout ({failure_code}): surface={surface_category}"
        )


class SlackSearchResult(BaseModel):
    """단일 Slack 검색 결과 항목."""

    result_index: int = Field(description="검색 결과 내 인덱스 (1-based)")
    query: str = Field(description="검색어")
    author: Optional[str] = Field(default=None, description="메시지 작성자")
    author_id: Optional[str] = Field(
        default=None,
        description="Canonical Slack author member ID (e.g. U...)",
        exclude=True,
        repr=False,
    )
    timestamp_raw: Optional[str] = Field(default=None, description="원시 타임스탬프 텍스트")
    text: str = Field(description="메시지 본문 요약 또는 전문")
    channel_name: Optional[str] = Field(default=None, description="메시지가 속한 실제 채널/DM명 (추측 불가 시 null)")
    channel_id: Optional[str] = Field(default=None, description="채널 ID (예: C3R53LYV8)")
    context: str = Field(default="search_result", description="컨텍스트 구분 (search_result)")
    mentions: list[str] = Field(default_factory=list, description="멘션(@) 목록")
    links: list[str] = Field(default_factory=list, description="포함된 URL 링크 목록")
    uia_message_id: Optional[str] = Field(default=None, description="UIA/DOM 메시지 고유 식별자 후보")
    result_url: Optional[str] = Field(default=None, description="검색 결과 퍼머링크 (archives URL)")
    gold_message_ts: Optional[str] = Field(default=None, description="Canonical Slack API message_ts (e.g. 1724738400.123456)")
    gold_thread_ts: Optional[str] = Field(default=None, description="Canonical Slack API thread_ts if thread reply")
    has_thread: bool = Field(default=False, description="스레드 댓글 존재 여부")
    reply_count: int = Field(default=0, description="스레드 댓글 수")
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
    """CDP 기반 백그라운드 Slack 검색 실행기 (Freshness Guard 및 Hidden Mount Bridge 포함)."""

    def __init__(
        self,
        cdp_adapter: Optional[SlackCdpAdapter] = None,
        lifecycle_manager: Optional[SlackLifecycleManager] = None,
        agent_window_manager: Optional[SlackAgentWindowManager] = None,
        cdp_port: int = 9222,
    ):
        self.lifecycle = lifecycle_manager or SlackLifecycleManager(cdp_port=cdp_port)
        self.cdp = cdp_adapter or SlackCdpAdapter(port=cdp_port, lifecycle_manager=self.lifecycle)
        self.agent_window_manager = agent_window_manager
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

    def _ensure_agent_ready(self) -> None:
        """Slack이 Agent Mode로 준비되었는지 검증하고, 미준비/Main Window Only 시 Fail-Closed 예외를 발생시킵니다."""
        status = self.lifecycle.get_status()
        if status.status != SlackAgentModeStatus.AGENT_READY:
            raise SlackNotReadyError(
                f"Slack이 Agent Mode로 준비되지 않았습니다. (현재 상태: {status.status.value})\n"
                f"안내: {status.message}"
            )

    async def open_search_ui(self, timeout_sec: float = 6.0) -> bool:
        """안전한 전역 검색창 활성화 (Agent Mode Readiness 검증 후 실행)."""
        self._ensure_agent_ready()
        if hasattr(self.cdp, "open_search_ui"):
            return await self.cdp.open_search_ui(timeout_sec=timeout_sec)
        return True

    async def execute_hidden_verified_search(self, query: str, timeout_sec: float = 8.0) -> dict[str, Any]:
        """Hidden Agent Window 대상 Non-Activating Mount Bridge를 통해 전역 검색을 수행합니다.
        
        보안 및 생명주기 흐름:
        1. Agent Window 상태 검증 (Adopted & Hidden)
        2. Fast path: 이미 Search UI가 마운트되어 있다면 브리지 생략
        3. Hidden mousedown 1회 디스패치 (_dispatch_verified_global_search_activation_mousedown)
        4. try:
               show_agent_window_no_activate() (SW_SHOWNOACTIVATE)
               _wait_for_verified_global_search_ui() (25ms bounded polling)
           finally:
               hide_agent_window() (SW_HIDE, 어떠한 실패 시에도 즉시 재은닉 보장)
        5. 타깃 연속성 검증 (same HWND/PID/Target)
        6. _set_and_submit_verified_global_search_on_open_ui(query) (모델 세터 + 네이티브 서브밋)
        """
        self._ensure_agent_ready()

        if self.agent_window_manager:
            state, adopted, msg = self.agent_window_manager.get_state()
            if state == SlackAgentWindowState.AGENT_WINDOW_ADOPTED_HIDDEN and adopted:
                hwnd = adopted.hwnd
                pid = adopted.pid

                # Fast path: Search UI already mounted
                is_mounted = await self.cdp._wait_for_verified_global_search_ui(
                    timeout_sec=0.05, poll_interval_sec=0.01
                )
                if is_mounted:
                    logger.debug("Search UI already mounted in hidden window. Proceeding to set & submit.")
                    return await self.cdp._set_and_submit_verified_global_search_on_open_ui(
                        query=query, timeout_sec=timeout_sec
                    )

                fg_before = self.agent_window_manager.get_foreground_window()

                # Step 1: Dispatch exact mousedown while hidden (1x)
                dis_res = await self.cdp._dispatch_verified_global_search_activation_mousedown()
                if not dis_res or not dis_res.get("ok"):
                    raise SlackSearchInputIdentityChangedError(
                        f"Hidden Search activation mousedown 실패: {dis_res}"
                    )

                # Step 2: Brief Non-Activating Mount Bridge in try ... finally
                try:
                    show_ok, show_msg = self.agent_window_manager.show_agent_window_no_activate()
                    if not show_ok:
                        raise RuntimeError(f"SW_SHOWNOACTIVATE 실행 실패: {show_msg}")

                    mounted = await self.cdp._wait_for_verified_global_search_ui(
                        timeout_sec=min(timeout_sec, 4.0),
                        poll_interval_sec=0.025,
                    )
                    if not mounted:
                        raise SlackSearchInputIdentityChangedError(
                            "SW_SHOWNOACTIVATE 브리지 중 Search UI 마운트 확인 실패 (timeout)."
                        )
                finally:
                    # Step 3: 즉시 SW_HIDE로 재은닉 (Finally 보장)
                    hide_ok, hide_msg = self.agent_window_manager.hide_agent_window()
                    if not hide_ok:
                        logger.warning(f"Search 브리지 후 SW_HIDE 재은닉 경고: {hide_msg}")

                # Step 4: Post-bridge Foreground & Window Identity Verification (BEFORE query set & submit)
                fg_after = self.agent_window_manager.get_foreground_window()
                if fg_before and fg_after and fg_before != fg_after and fg_after == hwnd:
                    raise SlackSearchInputIdentityChangedError(
                        f"Foreground continuity failure: SW_SHOWNOACTIVATE stole foreground focus (HWND {fg_after} == Agent HWND {hwnd})"
                    )

                state_after, adopted_after, _ = self.agent_window_manager.get_state()
                if not adopted_after or adopted_after.hwnd != hwnd or adopted_after.pid != pid:
                    raise SlackSearchInputIdentityChangedError(
                        f"Search 브리지 후 윈도우 식별자 불일치: HWND {hwnd} -> {getattr(adopted_after, 'hwnd', None)}"
                    )

                # Step 5: Query Setter + Native Submit on open UI
                return await self.cdp._set_and_submit_verified_global_search_on_open_ui(
                    query=query, timeout_sec=timeout_sec
                )

        # Fallback to standard visible execute_verified_global_search
        return await self.cdp.execute_verified_global_search(query=query, timeout_sec=timeout_sec)

    async def enter_query_and_search(self, query: str, timeout_sec: float = 4.0) -> dict[str, Any]:
        """검증된 전역 검색창(Quill Rich Editor)에 안전하게 쿼리를 설정하고 네이티브 커밋을 수행합니다."""
        self._ensure_agent_ready()
        return await self.execute_hidden_verified_search(query=query, timeout_sec=timeout_sec)

    async def parse_current_visible_results(self, query: str) -> list[SlackSearchResult]:
        """현재 DOM에 노출된 검색 결과 항목들을 검증된 Gold Identity 기반으로 정밀 파싱합니다."""
        from pesu_agent.search.slack_search_resolver import SlackSearchResultResolver

        raw_payload = await self.cdp.evaluate_js(SlackSearchResultResolver.JS_RESOLVE_RESULTS)
        resolution = SlackSearchResultResolver.resolve_from_dom_payload(raw_payload)
        return SlackSearchResultResolver.convert_to_slack_search_results(query, resolution)

    async def resolve_search_results_with_readiness(
        self, query: str, timeout_sec: float = 5.0, poll_interval_sec: float = 0.15
    ) -> tuple[Any, list[SlackSearchResult]]:
        """Bounded passive read-only polling readiness gate for search view surface resolution.

        Lifecycle semantics:
        - RESULTS: resolved immediately.
        - EMPTY_RESULTS: resolved immediately as valid empty collection.
        - LOADING / SEARCH_VIEW_UNKNOWN / FAIL_CLOSED (initial mount lag): passive read polling until timeout.
        - AMBIGUOUS: immediate fail-closed exception.
        - Exactly zero resubmits, zero query modifications, zero input domain dispatches.
        """
        from pesu_agent.search.slack_search_resolver import SlackSearchResultResolver

        start_t = asyncio.get_event_loop().time()
        last_resolution = None

        while asyncio.get_event_loop().time() - start_t < timeout_sec:
            raw_payload = await self.cdp.evaluate_js(SlackSearchResultResolver.JS_RESOLVE_RESULTS)
            resolution = SlackSearchResultResolver.resolve_from_dom_payload(raw_payload)
            last_resolution = resolution

            cat = resolution.surface_category
            if cat == "RESULTS":
                results = SlackSearchResultResolver.convert_to_slack_search_results(query, resolution)
                return resolution, results
            elif cat == "EMPTY_RESULTS":
                return resolution, []
            elif cat in ("AMBIGUOUS_EMPTY_AND_RESULTS", "AMBIGUOUS_LOADING_AND_RESULTS"):
                raise SlackCdpError(f"Ambiguous search surface state detected: {cat}")

            # Transient loading/mounting state: continue passive read polling
            await asyncio.sleep(poll_interval_sec)

        # Deadline reached while surface in transient state → explicit fail-closed exception.
        # This MUST NOT return [] to avoid masquerading as valid EMPTY_RESULTS.
        if last_resolution is None:
            raise SlackResultReadinessTimeoutError(
                failure_code="RESULT_READINESS_TIMEOUT_UNKNOWN",
                surface_category="FAIL_CLOSED",
                message="Result readiness timeout: no DOM response received during polling",
            )
        last_cat = last_resolution.surface_category
        if last_cat == "LOADING":
            raise SlackResultReadinessTimeoutError(
                failure_code="RESULT_READINESS_TIMEOUT_LOADING",
                surface_category=last_cat,
            )
        raise SlackResultReadinessTimeoutError(
            failure_code="RESULT_READINESS_TIMEOUT_UNKNOWN",
            surface_category=last_cat,
        )

    async def get_observed_query_state(self) -> tuple[str, bool]:
        """Slack 검색 UI에 현재 표시된 query 텍스트와 결과 0건 여부를 조회합니다."""
        js_observed = """
        (() => {
            const editorElem = document.querySelector('[data-qa="search_input_box"] [contenteditable="true"]') ||
                               document.querySelector('[data-qa="focusable_search_input"] [contenteditable="true"]') ||
                               document.querySelector('.c-search__input_box .ql-editor');
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

            const urlParams = new URLSearchParams(window.location.search);
            const urlQ = urlParams.get('q') || '';
            let decodedUrlQ = '';
            try { decodedUrlQ = decodeURIComponent(urlQ); } catch(e) { decodedUrlQ = urlQ; }

            const observedVal = editorVal || headerText || inputVal || decodedUrlQ;

            return {
                observed: observedVal,
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

            # 4. 전역 검색 UI 열기 (Visible 환경 호환성 유지)
            if not self.agent_window_manager or self.agent_window_manager.get_state()[0] != SlackAgentWindowState.AGENT_WINDOW_ADOPTED_HIDDEN:
                await self.open_search_ui()
                await asyncio.sleep(1.0)

            # 5. 검색어 입력 및 실행 (Hidden Bridge 또는 Native Submit 수행)
            enter_res = await self.enter_query_and_search(query, timeout_sec=8.0)
            if not enter_res.get("ok"):
                raise SlackCdpError(f"검색어 입력 실패: {json.dumps(enter_res, ensure_ascii=False)}")

            # 5. Query Verification Polling (read-only text check, separate from readiness)
            start_t = asyncio.get_event_loop().time()
            observed_query = ""
            query_verified = False

            while asyncio.get_event_loop().time() - start_t < timeout_sec:
                await asyncio.sleep(0.3)
                obs_q, _is_empty = await self.get_observed_query_state()
                observed_query = obs_q
                query_verified = bool(query.strip().lower() in observed_query.strip().lower())
                if query_verified:
                    break

            if not query_verified:
                logger.warning(
                    f"Query verification failed: requested={query!r}, observed={observed_query!r}"
                )

            # 6. Bounded Result Readiness Gate (single source of truth)
            #    Raises SlackResultReadinessTimeoutError on LOADING/UNKNOWN timeout.
            #    Raises SlackCdpError on AMBIGUOUS surface.
            #    Returns valid (resolution, results) for RESULTS or EMPTY_RESULTS.
            elapsed_query_check = asyncio.get_event_loop().time() - start_t
            remaining_readiness = max(timeout_sec - elapsed_query_check, 2.0)

            _resolution, initial_results = await self.resolve_search_results_with_readiness(
                query=query, timeout_sec=remaining_readiness, poll_interval_sec=0.15
            )

            # 7. Freshness Verification
            current_signature = self.compute_result_signature(query, initial_results)
            freshness_verified = True

            if prev_signature is not None and len(initial_results) > 0:
                if current_signature == prev_signature:
                    freshness_verified = False

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
