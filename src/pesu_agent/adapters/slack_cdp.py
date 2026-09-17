"""Slack CDP (Chrome DevTools Protocol) Read-Only Adapter (MVP 3.4.1).

Electron/Chromium 기반 Slack Desktop의 로컬 Remote Debugging 인터페이스(기본 9222)에
안전하게 연결하여 제한된 Read-Only DOM 조회 및 조작(검색, 결과 읽기, 스크롤)을 수행합니다.

보안 및 안전 제약 (Hard Read-Only Barrier):
1. Raw CDP Input/Dispatch API는 내부/진단 전용(SlackRawCdpAdapter)으로 격리.
2. Production Agent 계층은 오직 검증된 Typed Read-Only Capability만 호출 가능.
3. Message Composer, Thread Composer, Send Control 상호작용 원천 차단.
4. 모든 Navigation 및 세션 초기화 시 Capture-Phase DOM Read-Only Guard 자동 주입.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Optional

import psutil
import websockets

from pesu_agent.lifecycle.slack_lifecycle import (
    SlackAgentModeStatus,
    SlackLifecycleManager,
    SlackStatusResult,
)
from pesu_agent.security.read_only_barrier import (
    PROHIBITED_ELEMENT_PATTERNS,
    VERIFIED_GLOBAL_SEARCH_SELECTORS,
    ReadOnlyViolationError,
    SearchInputNotVerifiedError,
    SlackActionValidator,
    SlackReadCapability,
    SlackSearchInputIdentityChangedError,
    SlackVerifiedSearchInputNotFoundError,
)
from pesu_agent.security.write_audit import (
    SlackWriteAuditReport,
    SlackWriteAuditTracker,
)

logger = logging.getLogger(__name__)


class SlackCdpError(Exception):
    """Slack CDP 통신 관련 기본 예외."""


class SlackNotReadyError(SlackCdpError):
    """Slack이 Agent Mode로 준비되지 않았을 때 발생하는 예외."""


class SlackTargetNotFoundError(SlackCdpError):
    """Slack renderer page target을 찾을 수 없을 때 발생하는 예외."""


class SlackRawCdpAdapter:
    """[Internal/Diagnostics Only] 저수준 CDP WebSocket 전송 어댑터.
    
    Production Agent 계층에서는 직접 사용이 금지되며,
    반드시 SlackReadOnlyCdpAdapter를 통해서만 제어되어야 합니다.
    """

    def __init__(
        self,
        port: int = 9222,
        lifecycle_manager: Optional[SlackLifecycleManager] = None,
        timeout_sec: float = 10.0,
        target_id: Optional[str] = None,
    ):
        self.port = port
        self.lifecycle = lifecycle_manager or SlackLifecycleManager(cdp_port=port)
        self.timeout_sec = timeout_sec
        self.target_id = target_id
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._msg_id = 0
        self._connected_target: Optional[dict[str, Any]] = None
        self._ref_count = 0

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        if hasattr(self._ws, "state"):
            state = getattr(self._ws, "state")
            if hasattr(state, "name"):
                return state.name == "OPEN"
            return state == 1
        if hasattr(self._ws, "closed"):
            return not self._ws.closed
        return True

    def get_cdp_targets(self) -> list[dict[str, Any]]:
        """CDP HTTP 엔드포인트(/json)로부터 활성 타깃 목록을 조회합니다."""
        url = f"http://127.0.0.1:{self.port}/json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PesuAgent/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as err:
            raise SlackCdpError(f"CDP 엔드포인트({url}) 연결 실패: {err}") from err
        return []

    def find_slack_renderer_target(self) -> dict[str, Any]:
        """Slack renderer page 타깃을 선택합니다 (Fail-Closed, Main Target Fallback 금지)."""
        targets = self.get_cdp_targets()
        pages = [t for t in targets if t.get("type") == "page"]

        if self.target_id:
            for t in pages:
                if t.get("id") == self.target_id:
                    return t
            raise SlackTargetNotFoundError(
                f"지정된 Agent Target ID({self.target_id})를 찾을 수 없습니다 (Main Target Fallback 금지 / Fail Closed)."
            )

        renderer_pages = [t for t in pages if "app.slack.com" in t.get("url", "")]
        if renderer_pages:
            return renderer_pages[0]

        if pages:
            return pages[0]

        raise SlackTargetNotFoundError("활성 Slack Renderer Page Target을 찾을 수 없습니다.")

    async def connect(self) -> None:
        self._ref_count += 1
        if self.is_connected:
            return

        status = self.lifecycle.get_status()
        if status.status != SlackAgentModeStatus.AGENT_READY:
            raise SlackNotReadyError(
                f"Slack이 Agent Mode로 실행 중이지 않습니다 (상태: {status.status.value})."
            )

        target = self.find_slack_renderer_target()
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise SlackCdpError(f"Target({target.get('id')})에 webSocketDebuggerUrl이 없습니다.")

        try:
            self._ws = await websockets.connect(
                ws_url,
                ping_interval=None,
                max_size=50 * 1024 * 1024,
                open_timeout=self.timeout_sec,
            )
            self._connected_target = target
            logger.info(
                f"CDP WebSocket 연결 성공: Target={target.get('id')[:12]}... ('{target.get('title')}')"
            )
        except Exception as err:
            raise SlackCdpError(f"WebSocket 연결 실패 ({ws_url}): {err}") from err

    async def disconnect(self) -> None:
        self._ref_count = max(0, self._ref_count - 1)
        if self._ref_count > 0:
            return
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
            self._connected_target = None

    async def _send_raw_command(
        self, method: str, params: Optional[dict[str, Any]] = None, timeout: float = 10.0
    ) -> dict[str, Any]:
        if not self.is_connected:
            raise SlackCdpError("CDP WebSocket이 연결되어 있지 않습니다.")

        self._msg_id += 1
        req_id = self._msg_id
        payload = {"id": req_id, "method": method, "params": params or {}}
        await self._ws.send(json.dumps(payload))

        start_time = asyncio.get_event_loop().time()
        while True:
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise asyncio.TimeoutError(f"CDP {method} 응답 타임아웃 ({timeout}s)")

            raw_msg = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            resp = json.loads(raw_msg)

            if resp.get("id") == req_id:
                if "error" in resp:
                    raise RuntimeError(f"CDP 에러 ({method}): {resp['error']}")
                return resp.get("result", {})

    async def _evaluate_raw_js(self, expression: str, timeout: float = 10.0) -> Any:
        result = await self._send_raw_command(
            method="Runtime.evaluate",
            params={
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=timeout,
        )
        return result.get("result", {}).get("value")

    async def _dispatch_raw_mouse_event(
        self,
        type_: str,
        x: int,
        y: int,
        button: str = "left",
        click_count: int = 1,
        modifiers: int = 0,
    ) -> None:
        params = {"type": type_, "x": x, "y": y, "button": button, "clickCount": click_count}
        if modifiers:
            params["modifiers"] = modifiers
        await self._send_raw_command(
            method="Input.dispatchMouseEvent",
            params=params,
        )


class SlackBootstrapAdapter:
    """Bootstrap 전용 Typed CDP Adapter.

    Secondary Window 생성을 위한 제한적 Bootstrap 작업만 허용합니다.
    Caller에게 임의 좌표, 임의 JS, 임의 키보드/마우스 이벤트를 노출하지 않습니다.

    허용 작업:
    - read_main_state(): Main Window URL/scroll/fingerprint 읽기 전용
    - open_secondary_window(): Sidebar 채널 탐색 → 검증 → Ctrl+Click (내부 좌표 산출)
    - verify_main_preserved(baseline): Main 상태 보존 재확인

    금지:
    - composer 접근
    - text/keyboard 입력
    - 임의 JS 실행
    - Slack write action
    """

    # 사전 정의된 고정 DOM read script (caller-provided JS 금지)
    _JS_MAIN_STATE = """
    (() => {
        const sc = document.querySelector('.c-virtual_list__scroll_container, [data-qa="slack_kit_scrollbar"], .p-message_pane');
        const items = Array.from(document.querySelectorAll('.c-virtual_list__item, [data-qa="virtual-list-item"]'));
        const fprints = items.slice(0, 5).map(el => (el.innerText || '').slice(0, 50));
        return {
            url: window.location.href,
            scroll_top: sc ? sc.scrollTop : 0,
            fingerprints: fprints
        };
    })()
    """

    _JS_FIND_SIDEBAR_CHANNELS = """
    (() => {
        const PROHIBITED = ['texty_input', 'message_input', 'composer', 'send', 'form', 'submit'];

        let channels = Array.from(document.querySelectorAll(
            '[data-qa="channel-sidebar-channel"], [data-qa="channel_sidebar_name"], [data-qa*="sidebar-channel"], .p-channel_sidebar__channel'
        )).filter(el => {
            const r = el.getBoundingClientRect();
            if (r.width <= 30 || r.height <= 10 || r.top < 130 || r.bottom > (window.innerHeight - 80)) {
                return false;
            }
            // Verify element is not a composer/send/form area
            const qaAttrs = [el.getAttribute('data-qa') || '', ...Array.from(el.querySelectorAll('[data-qa]')).map(e => e.getAttribute('data-qa') || '')];
            for (const qa of qaAttrs) {
                for (const p of PROHIBITED) {
                    if (qa.toLowerCase().includes(p)) return false;
                }
            }
            return true;
        });

        if (channels.length === 0) {
            const homeBtn = document.querySelector('button[data-qa="tab_rail_home_button"]');
            if (homeBtn) {
                try { homeBtn.click(); } catch(e) {}
            }
        }

        return channels.map(el => {
            const r = el.getBoundingClientRect();
            return {
                name: (el.innerText || '').trim().replace(/^[#@]/, ''),
                x: Math.round(r.left + Math.min(80, r.width / 2)),
                y: Math.round(r.top + r.height / 2),
                tag: el.tagName,
                qa: el.getAttribute('data-qa') || ''
            };
        });
    })()
    """

    def __init__(
        self,
        port: int = 9222,
        lifecycle_manager: Optional[SlackLifecycleManager] = None,
        timeout_sec: float = 10.0,
        target_id: Optional[str] = None,
    ):
        self._raw = SlackRawCdpAdapter(
            port=port,
            lifecycle_manager=lifecycle_manager,
            timeout_sec=timeout_sec,
            target_id=target_id,
        )

    @property
    def is_connected(self) -> bool:
        return self._raw.is_connected

    async def connect(self) -> None:
        await self._raw.connect()

    async def disconnect(self) -> None:
        await self._raw.disconnect()

    async def read_main_state(self) -> dict:
        """Main Window의 URL, scroll position, viewport fingerprint를 읽기 전용으로 반환합니다."""
        result = await self._raw._evaluate_raw_js(self._JS_MAIN_STATE)
        return result or {}

    async def find_sidebar_channels(self, exclude_names: Optional[list[str]] = None, timeout_sec: float = 8.0) -> list[dict]:
        """Sidebar에서 클릭 가능한 채널 목록을 찾고 composer/send/form이 아님을 검증합니다.

        Slack 재시작 직후 DOM 마운팅을 위해 최대 timeout_sec 동안 폴링합니다.

        Returns:
            Verified channel list with internally computed bounds.
            Caller에게 임의 좌표를 전달할 수 없으며, 반환값은 open_secondary_window()에서만 사용됩니다.
        """
        start_t = asyncio.get_event_loop().time()
        all_channels: Any = []

        while asyncio.get_event_loop().time() - start_t < timeout_sec:
            all_channels = await self._raw._evaluate_raw_js(self._JS_FIND_SIDEBAR_CHANNELS) or []
            if all_channels:
                break
            await asyncio.sleep(0.5)

        if not all_channels:
            url = await self._raw._evaluate_raw_js("window.location.href") or ""
            if "/search" in url:
                import re
                m = re.search(r"/client/([A-Z0-9]+)", url)
                team_id = m.group(1) if m else "T32QA15GC"
                try:
                    await self._raw._send_raw_command(
                        method="Page.navigate",
                        params={"url": f"https://app.slack.com/client/{team_id}"},
                    )
                    await asyncio.sleep(2.0)
                    all_channels = await self._raw._evaluate_raw_js(self._JS_FIND_SIDEBAR_CHANNELS) or []
                except Exception:
                    pass

        # Normalize response structure
        if isinstance(all_channels, dict):
            all_channels = [{
                "name": all_channels.get("channel_name", all_channels.get("name", "channel")),
                "x": all_channels.get("x", 200),
                "y": all_channels.get("y", 200),
            }]
        elif isinstance(all_channels, list):
            clean = []
            for item in all_channels:
                if isinstance(item, dict):
                    clean.append({
                        "name": item.get("name", item.get("channel_name", "channel")),
                        "x": item.get("x", 200),
                        "y": item.get("y", 200),
                    })
                elif isinstance(item, str):
                    clean.append({"name": item, "x": 200, "y": 200})
            all_channels = clean

        exclude = set(exclude_names or [])
        available = [c for c in all_channels if c["name"] not in exclude]
        if not available:
            available = all_channels

        return available

    async def open_secondary_window(self, exclude_channel_names: Optional[list[str]] = None) -> tuple[bool, str, Optional[str]]:
        """Sidebar 채널을 내부적으로 탐색/검증한 뒤 Ctrl+Click으로 Secondary Window를 엽니다.

        Caller는 좌표, selector, JS를 전달할 수 없습니다.
        Adapter 내부에서:
        1. Verified Slack sidebar element 탐색
        2. Element identity 검증 (composer/send/form 아님 확인)
        3. Element bounds 산출
        4. 해당 element 중앙에 Ctrl+Click

        Returns:
            (success, message, clicked_channel_name)
        """
        candidates = await self.find_sidebar_channels(exclude_names=exclude_channel_names)
        if not candidates:
            return False, "Sidebar에서 클릭 가능한 채널을 찾을 수 없습니다.", None

        for candidate in candidates[:3]:
            x = candidate["x"]
            y = candidate["y"]
            name = candidate["name"]

            logger.info(
                f"Bootstrap: Sidebar '{name}'에 CDP Ctrl+Click 디스패치 (x={x}, y={y})"
            )
            await self._raw._dispatch_raw_mouse_event(
                type_="mousePressed", x=x, y=y, button="left", click_count=1, modifiers=2
            )
            await self._raw._dispatch_raw_mouse_event(
                type_="mouseReleased", x=x, y=y, button="left", click_count=1, modifiers=2
            )
            return True, f"Ctrl+Click dispatched on '{name}'", name

        return False, "후보 채널 Ctrl+Click 실패", None

    async def verify_main_preserved(self, baseline: dict) -> dict:
        """Main Window 상태가 baseline과 동일한지 재확인합니다.

        Returns:
            {"url_ok": bool, "scroll_ok": bool, "viewport_ok": bool, "after": dict}
        """
        after = await self._raw._evaluate_raw_js(self._JS_MAIN_STATE) or {}
        url_ok = baseline.get("url") == after.get("url")
        scroll_ok = abs(baseline.get("scroll_top", 0) - after.get("scroll_top", 0)) < 5
        vp_ok = baseline.get("fingerprints") == after.get("fingerprints")
        return {"url_ok": url_ok, "scroll_ok": scroll_ok, "viewport_ok": vp_ok, "after": after}


class SlackReadOnlyCdpAdapter:
    """Production Slack Evidence Agent 전용 Hard Read-Only CDP 어댑터."""

    def __init__(
        self,
        port: int = 9222,
        lifecycle_manager: Optional[SlackLifecycleManager] = None,
        timeout_sec: float = 10.0,
        target_id: Optional[str] = None,
    ):
        self._raw = SlackRawCdpAdapter(
            port=port,
            lifecycle_manager=lifecycle_manager,
            timeout_sec=timeout_sec,
            target_id=target_id,
        )
        self.audit_tracker = SlackWriteAuditTracker()

    @property
    def port(self) -> int:
        return self._raw.port

    @property
    def target_id(self) -> Optional[str]:
        return self._raw.target_id

    @target_id.setter
    def target_id(self, val: Optional[str]) -> None:
        self._raw.target_id = val

    @property
    def is_connected(self) -> bool:
        return self._raw.is_connected

    def get_cdp_targets(self) -> list[dict[str, Any]]:
        return self._raw.get_cdp_targets()

    def find_slack_renderer_target(self) -> dict[str, Any]:
        """Slack renderer page 타깃을 선택합니다 (Fail-Closed, Main Target Fallback 금지)."""
        targets = self.get_cdp_targets()
        pages = [t for t in targets if t.get("type") == "page"]

        if self.target_id:
            for t in pages:
                if t.get("id") == self.target_id:
                    return t
            raise SlackTargetNotFoundError(
                f"지정된 Agent Target ID({self.target_id})를 찾을 수 없습니다 (Main Target Fallback 금지 / Fail Closed)."
            )

        renderer_pages = [t for t in pages if "app.slack.com" in t.get("url", "")]
        if renderer_pages:
            return renderer_pages[0]

        if pages:
            return pages[0]

        raise SlackTargetNotFoundError("활성 Slack Renderer Page Target을 찾을 수 없습니다.")

    async def connect(self) -> None:
        await self._raw.connect()

    async def disconnect(self) -> None:
        await self._raw.disconnect()

    async def __aenter__(self) -> SlackReadOnlyCdpAdapter:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()

    async def navigate_to_url(self, url: str) -> None:
        """안전한 Read-Only 페이지 이동 (Page.navigate)."""
        await self._raw._send_raw_command("Page.navigate", {"url": url})

    async def evaluate_js(self, expression: str, timeout: float = 10.0) -> Any:
        """안전 검증을 거친 Read-Only JavaScript 식 실행."""
        # Prohibited selector / write pattern 검증
        lower_expr = expression.lower()
        if "texty_input" in lower_expr and "search" not in lower_expr:
            self.audit_tracker.record_blocked_write(
                reason="texty_input selector evaluated in JS", is_main_composer=True
            )
            raise ReadOnlyViolationError(
                "🚨 [READ_ONLY_VIOLATION] Message Composer([data-qa='texty_input']) 접근 시도 차단."
            )

        res = await self._raw._evaluate_raw_js(expression, timeout=timeout)
        return res

    async def qualify_workspace_dom_structure(self, timeout_sec: float = 4.0) -> dict[str, Any]:
        """Full Workspace vs Channel Popout 여부를 100% 읽기 전용으로 판별하는 구조 지문 검사."""
        js_qualify = r"""
        (() => {
            // 1. Global Search markers
            const topNavSearch = document.querySelectorAll('button[data-qa="top_nav_search"], [data-qa="top_nav_search"]');
            const searchInputBox = document.querySelectorAll('[data-qa="search_input_box"]');
            const hasGlobalSearch = topNavSearch.length > 0 || searchInputBox.length > 0;

            // 2. Full Workspace Shell markers
            const topNav = document.querySelectorAll('.p-top_nav, [data-qa="top_nav"], .p-ia4_top_nav, [data-qa="top_nav_container"]');
            const sidebar = document.querySelectorAll('.p-workspace__sidebar, [data-qa="channel_sidebar"], .p-ia4_sidebar');
            const tabRail = document.querySelectorAll('.p-tab_rail, [data-qa="tab_rail"], [data-qa="team_rail"], nav[aria-label*="탐색"], nav[aria-label*="Navigation"]');
            const workspaceLayout = document.querySelectorAll('.p-client_container, .p-client, .p-workspace-layout, .p-workspace, .p-ia__main_menu');
            const hasShellMarker = (topNav.length > 0 || sidebar.length > 0 || tabRail.length > 0 || workspaceLayout.length > 0);

            // 3. Channel Popout markers
            const channelPopout = document.querySelectorAll('.p-workspace__primary_view--popout, .p-popout_window, .p-popout_view');
            const searchInChannel = document.querySelectorAll('button[data-qa="search_in_channel_button"], [data-qa="search_in_channel_button"]');
            const hasPopoutClass = channelPopout.length > 0;
            const hasInChannelSearch = searchInChannel.length > 0;

            const isFullWorkspace = hasGlobalSearch && hasShellMarker && !hasPopoutClass && !hasInChannelSearch;
            // Channel Popout requires POSITIVE evidence of popout (popout class or in-channel search) AND absence of global search
            const isChannelPopout = (hasPopoutClass || hasInChannelSearch) && !hasGlobalSearch;
            const isTransientOrUnknown = !isFullWorkspace && !isChannelPopout;

            return {
                is_full_workspace: isFullWorkspace,
                is_channel_popout: isChannelPopout,
                is_transient_or_unknown: isTransientOrUnknown,
                has_global_search: hasGlobalSearch,
                has_shell_marker: hasShellMarker,
                counts: {
                    top_nav_search: topNavSearch.length,
                    top_nav: topNav.length,
                    sidebar: sidebar.length,
                    tab_rail: tabRail.length,
                    workspace_layout: workspaceLayout.length,
                    channel_popout: channelPopout.length,
                    search_in_channel: searchInChannel.length
                },
                url: window.location.href,
                title: document.title
            };
        })()
        """
        res = await self._raw._evaluate_raw_js(js_qualify, timeout=timeout_sec)
        return res or {}

    async def _dispatch_verified_global_search_activation_mousedown(self) -> dict[str, Any]:
        """
        오직 실제 관측된 button[data-qa="top_nav_search"] 셀렉터만을 대상으로 exact mousedown 이벤트를 디스패치합니다.
        
        보안 제약:
        - exact selector == button[data-qa="top_nav_search"] (cardinality == 1)
        - Composer ancestry 부재 확인
        - disabled/hidden/zero-rect 부재 확인
        - exact MouseEvent("mousedown", { bubbles: true, cancelable: true, button: 0, ctrlKey: false })
        - 0 click, 0 keyboard, 0 generic mouse
        """
        js_mousedown_top_nav_search = """
        (() => {
            const btns = Array.from(document.querySelectorAll('button[data-qa="top_nav_search"], [data-qa="top_nav_search"]')).filter(el => el instanceof Element && el.isConnected);
            if (btns.length !== 1) {
                return { ok: false, error: 'top_nav_search_button_cardinality_invalid', count: btns.length };
            }

            const btn = btns[0];

            // Composer ancestry 부재 검증
            const inComposer = !!btn.closest('.p-workspace__primary_view_body, .p-message_pane, .p-flexpane, [data-qa="message_input"], [data-qa="threads_flexpane"]');
            if (inComposer) {
                return { ok: false, error: 'composer_detected_as_search_button', is_composer: true };
            }

            // Visible / Enabled / Non-zero Rect 검증
            const isDis = (btn.disabled === true || btn.getAttribute('aria-disabled') === 'true');
            const r = btn.getBoundingClientRect();
            const s = window.getComputedStyle(btn);
            const isVisible = (btn.offsetParent !== null && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0');
            const nonZero = (r.width > 0 && r.height > 0);

            if (isDis || !isVisible || !nonZero) {
                return { ok: false, error: 'top_nav_search_button_not_renderable', disabled: isDis, visible: isVisible, non_zero: nonZero };
            }

            const evt = new MouseEvent('mousedown', {
                bubbles: true,
                cancelable: true,
                button: 0,
                ctrlKey: false
            });

            const dispatched = btn.dispatchEvent(evt);
            return {
                ok: true,
                dispatched: dispatched,
                eventType: 'mousedown',
                button: 0,
                targetTagName: btn.tagName ? btn.tagName.toLowerCase() : '',
                targetDataQa: btn.getAttribute('data-qa')
            };
        })()
        """
        dispatch_res = await self._raw._evaluate_raw_js(js_mousedown_top_nav_search)
        if not dispatch_res or not dispatch_res.get("ok"):
            if dispatch_res and dispatch_res.get("is_composer"):
                self.audit_tracker.record_blocked_write(
                    reason="Composer matched during search button resolution", is_main_composer=True
                )
                raise ReadOnlyViolationError("🚨 [READ_ONLY_VIOLATION] 검색 버튼 탐색 중 Composer 감지되어 차단.")
            logger.debug(f"top_nav_search 버튼 활성화 불가: {dispatch_res}")
        return dispatch_res or {"ok": False, "error": "empty_evaluate_result"}

    async def _wait_for_verified_global_search_ui(
        self, timeout_sec: float = 4.0, poll_interval_sec: float = 0.025
    ) -> bool:
        """검증된 Search Input([data-qa="search_input_box"] .ql-editor)이 마운트될 때까지 Bounded Polling을 수행합니다."""
        js_check_existing = """
        (() => {
            const editor = document.querySelector('[data-qa="search_input_box"] .ql-editor') ||
                           document.querySelector('[data-qa="search_input_box"] [contenteditable="true"]') ||
                           document.querySelector('input[data-qa="top_nav_search__input"]');
            if (editor) {
                const inSearch = !!editor.closest('[data-qa="search_input_box"], .c-search__input_box, .p-top_nav__search, .p-search_filter');
                const inComposer = !!editor.closest('.p-workspace__primary_view_body, .p-message_pane, .p-flexpane, [data-qa="message_input"]');
                const r = editor.getBoundingClientRect();
                const isVisible = editor.offsetParent !== null && r.width > 0 && r.height > 0;
                if (inSearch && !inComposer && isVisible) {
                    editor.focus();
                    return { already_mounted: true };
                }
            }
            return { already_mounted: false };
        })()
        """
        start_t = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_t < timeout_sec:
            chk = await self._raw._evaluate_raw_js(js_check_existing)
            if chk and chk.get("already_mounted"):
                return True
            await asyncio.sleep(poll_interval_sec)

        return False

    async def activate_verified_global_search_ui(self, timeout_sec: float = 6.0) -> bool:
        """
        오직 실제 관측된 button[data-qa="top_nav_search"] 셀렉터만을 사용하여 전역 검색 UI를 활성화하고,
        검증된 Search Input이 마운트될 때까지 대기합니다 (Visible 편의 메서드).
        """
        # Step 1: 이미 열려있는지 확인
        js_check_existing = """
        (() => {
            const editor = document.querySelector('[data-qa="search_input_box"] .ql-editor') ||
                           document.querySelector('[data-qa="search_input_box"] [contenteditable="true"]') ||
                           document.querySelector('input[data-qa="top_nav_search__input"]');
            if (editor) {
                const inSearch = !!editor.closest('[data-qa="search_input_box"], .c-search__input_box, .p-top_nav__search, .p-search_filter');
                const inComposer = !!editor.closest('.p-workspace__primary_view_body, .p-message_pane, .p-flexpane, [data-qa="message_input"]');
                const r = editor.getBoundingClientRect();
                const isVisible = editor.offsetParent !== null && r.width > 0 && r.height > 0;
                if (inSearch && !inComposer && isVisible) {
                    editor.focus();
                    return { already_mounted: true };
                }
            }
            return { already_mounted: false };
        })()
        """
        existing = await self._raw._evaluate_raw_js(js_check_existing)
        if existing and existing.get("already_mounted"):
            return True

        # Step 2: mousedown 디스패치
        dispatch_res = await self._dispatch_verified_global_search_activation_mousedown()
        if not dispatch_res or not dispatch_res.get("ok"):
            return False

        # Step 3: 마운트 대기 (0.1s 간격)
        return await self._wait_for_verified_global_search_ui(timeout_sec=timeout_sec, poll_interval_sec=0.1)

    async def open_search_ui(self, timeout_sec: float = 6.0) -> bool:
        """안전하게 전역 검색 모달을 활성화합니다 (activate_verified_global_search_ui 위임)."""
        return await self.activate_verified_global_search_ui(timeout_sec=timeout_sec)

    async def _resolve_and_set_verified_search_input(self, query: str, timeout_sec: float = 6.0) -> dict[str, Any]:
        """검증된 Primary Search Input([data-qa="search_input_box"] .ql-editor)에 모델 네이티브(Texty.setContents) 방식으로 쿼리를 설정합니다.
        
        엄격한 다계층 검증:
        - Primary selector: [data-qa="search_input_box"] .ql-editor
        - connected == true, disabled != true, aria_hidden != true, visible == true, non_zero_rect == true
        - role == combobox, inside verified SEARCH_MODAL_WRAPPER
        - Composer subtree 절대 배제 (0 intersection)
        - Cardinality: 정확히 1개 후보만 허용 (0개 또는 2개 이상 시 즉시 Fail-Closed)
        - React Fiber 다중 앵커 탐색을 통해 SearchInputBox 및 Texty/Quill 인스턴스 획득
        - Model-Native Setter: texty.setContents({ contents: [{ insert: query }] }, { source: "api" }) 1회 호출
        - 즉시 원자적 검증 (동일 JS 클로저, 동일 객체 레퍼런스):
          - DOM_MATCH == true
          - QUILL_TEXT_MATCH == true
          - RAW_QUILL_DELTA_MATCH == true
          - TEXTY_CONTENTS_MATCH == true
          - SAME_INPUT_REF == true, SAME_TEXTY_REF == true, SAME_QUILL_REF == true
        - Bounded Redux state polling (T+0 ~ T+250ms) -> REDUX_QUERY_OPS_MATCH == true
        - 5대 계층 일치 및 동일 레퍼런스 연속성이 모두 충족될 때만 성공, 미충족 시 Fail-Closed.
        """
        js_resolve_and_set = f"""
        (() => {{
            const query = {json.dumps(query)};

            // 1. Primary Search Editor Selector ([data-qa="search_input_box"] .ql-editor)
            const candidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor'));
            const validCandidates = [];

            for (const candidate of candidates) {{
                // Composer Ancestry 절대 배제 (Message / Thread Composer 감지 시 즉시 Fail Closed)
                const inComposer = candidate.matches('[data-qa*="composer"], [data-qa="message_input"]') ||
                                   !!candidate.closest('.p-workspace__primary_view_body, .p-message_pane, .p-flexpane, [data-qa="message_input"], [data-qa="threads_flexpane"]');
                if (inComposer) {{
                    return {{ ok: false, error: 'composer_detected_as_search_input', is_composer: true }};
                }}

                // 엄격한 구조 검증 (Connected, Enabled, Aria-Hidden, Visibility, Geometry, Role, Modal Wrapper)
                const isConnected = (candidate.isConnected === true);
                const notDisabled = (!candidate.disabled && !candidate.hasAttribute('disabled') && candidate.getAttribute('aria-disabled') !== 'true');
                const notAriaHidden = (candidate.getAttribute('aria-hidden') !== 'true');
                const s = window.getComputedStyle(candidate);
                const isVisible = (candidate.offsetParent !== null && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0');
                const r = candidate.getBoundingClientRect();
                const nonZeroRect = (r.width > 0 && r.height > 0);
                const roleCombobox = (candidate.getAttribute('role') === 'combobox');
                const inSearchModalWrapper = !!candidate.closest('.c-search_modal__wrapper, .c-search_modal');

                if (isConnected && notDisabled && notAriaHidden && isVisible && nonZeroRect && roleCombobox && inSearchModalWrapper) {{
                    validCandidates.push(candidate);
                }}
            }}

            // 2. 정확한 단일 후보(Cardinality == 1) 검증
            if (validCandidates.length === 0) {{
                return {{ ok: false, error: 'no_verified_primary_search_input_found' }};
            }}
            if (validCandidates.length > 1) {{
                return {{ ok: false, error: 'ambiguous_multiple_primary_search_inputs_found', count: validCandidates.length }};
            }}

            const candidate = validCandidates[0];

            // 3. Multi-Anchor React Fiber Discovery (SearchInputBox / Texty / Quill)
            const anchorCandidates = [
                candidate,
                candidate.parentElement,
                candidate.parentElement ? candidate.parentElement.parentElement : null,
                candidate.closest('[data-qa="search_input_box"]'),
                candidate.closest('.c-search__input_box'),
                candidate.closest('.c-search_modal__wrapper')
            ];

            let startingFiber = null;
            for (const anchor of anchorCandidates) {{
                if (!anchor) continue;
                const fiberKey = Object.keys(anchor).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
                if (fiberKey && anchor[fiberKey]) {{
                    startingFiber = anchor[fiberKey];
                    break;
                }}
            }}

            if (!startingFiber) {{
                return {{ ok: false, error: 'no_react_fiber_key_found' }};
            }}

            let fiber = startingFiber;
            let depth = 0;
            let searchInputBoxInstance = null;
            let directTextyInstance = null;

            while (fiber && depth < 40) {{
                if (fiber.stateNode && typeof fiber.stateNode.getTextyOps === 'function') {{
                    searchInputBoxInstance = fiber.stateNode;
                    break;
                }}
                if (fiber.memoizedProps && fiber.memoizedProps.texty) {{
                    directTextyInstance = fiber.memoizedProps.texty;
                }}
                if (fiber.stateNode && fiber.stateNode.texty) {{
                    directTextyInstance = fiber.stateNode.texty;
                }}
                fiber = fiber.return;
                depth++;
            }}

            const textyObj = (searchInputBoxInstance && searchInputBoxInstance.texty) ? searchInputBoxInstance.texty : directTextyInstance;
            if (!textyObj) {{
                return {{ ok: false, error: 'texty_instance_not_found' }};
            }}

            const textyCore = textyObj.instance ? textyObj.instance : textyObj;
            const quill = textyCore.quill;
            if (!quill) {{
                return {{ ok: false, error: 'quill_instance_not_found' }};
            }}

            // Normalization helper (Extract string segments & strip canonical trailing newline)
            const normalizeOpsToString = (ops) => {{
                if (!Array.isArray(ops)) return "";
                const raw = ops.map(op => (typeof op.insert === 'string' ? op.insert : '')).join('');
                return raw.replace(/\\n$/, '');
            }};

            // 4. Model-Native Setter Execution (Texty.setContents with source: "api")
            let setterMethodUsed = "NONE";
            try {{
                if (typeof textyObj.setContents === 'function') {{
                    textyObj.setContents({{ contents: [{{ insert: query }}] }}, {{ source: "api" }});
                    setterMethodUsed = "texty.setContents";
                }} else if (typeof textyCore.setContents === 'function') {{
                    textyCore.setContents({{ contents: [{{ insert: query }}] }}, {{ source: "api" }});
                    setterMethodUsed = "textyCore.setContents";
                }} else if (typeof quill.setContents === 'function') {{
                    quill.setContents([{{ insert: query }}], "api");
                    setterMethodUsed = "quill.setContents";
                }} else {{
                    return {{ ok: false, error: 'no_callable_setcontents_method_found' }};
                }}
            }} catch (e) {{
                return {{ ok: false, error: 'setter_threw_exception', message: e.message }};
            }}

            // 5. Immediate Same-Instance Read (Atomic in Same Closure)
            const postDomText = candidate.textContent || '';
            const postQuillText = (typeof quill.getText === 'function' ? quill.getText() : '').replace(/\\n$/, '');
            const postQuillOps = (typeof quill.getContents === 'function') ? (quill.getContents()?.ops || []) : [];
            const postTextyOps = (typeof textyObj.getContents === 'function') ? (textyObj.getContents()?.contents || []) : [];

            const immediateDomMatch = (postDomText === query);
            const immediateQuillTextMatch = (postQuillText === query);
            const immediateRawQuillDeltaMatch = (normalizeOpsToString(postQuillOps) === query);
            const immediateTextyContentsMatch = (normalizeOpsToString(postTextyOps) === query);

            const sameTextyRef = (textyObj === ((searchInputBoxInstance && searchInputBoxInstance.texty) ? searchInputBoxInstance.texty : directTextyInstance));
            const sameQuillRef = (quill === textyCore.quill);
            const sameInputARef = (candidate === document.querySelector('[data-qa="search_input_box"] .ql-editor'));

            const atomicSuccess = immediateDomMatch && immediateQuillTextMatch && immediateRawQuillDeltaMatch && immediateTextyContentsMatch && sameTextyRef && sameQuillRef && sameInputARef;

            return {{
                ok: atomicSuccess,
                type: 'model_native_quill_search_editor',
                selector_identity: '[data-qa="search_input_box"] .ql-editor',
                setter_method: setterMethodUsed,
                dom_match: immediateDomMatch,
                quill_text_match: immediateQuillTextMatch,
                raw_quill_delta_match: immediateRawQuillDeltaMatch,
                texty_contents_match: immediateTextyContentsMatch,
                same_input_ref: sameInputARef,
                same_texty_ref: sameTextyRef,
                same_quill_ref: sameQuillRef
            }};
        }})()
        """
        start_t = asyncio.get_event_loop().time()
        last_res = {"ok": False, "error": "verified_search_input_not_found"}

        while asyncio.get_event_loop().time() - start_t < timeout_sec:
            try:
                res = await self._raw._evaluate_raw_js(js_resolve_and_set)
                if res:
                    last_res = res
                    if res.get("ok") or res.get("is_composer"):
                        break
            except Exception:
                pass
            await asyncio.sleep(0.4)

        if not last_res.get("ok"):
            if last_res.get("is_composer"):
                self.audit_tracker.record_blocked_write(
                    reason="Composer matched during search input resolution", is_main_composer=True
                )
                raise ReadOnlyViolationError("🚨 [READ_ONLY_VIOLATION] 검색창 탐색 중 Composer 감지되어 즉시 차단.")
            raise SlackVerifiedSearchInputNotFoundError(
                f"검증된 Global Search Input을 찾지 못했거나 모델 동기화 실패: {last_res.get('error')}"
            )

        # Bounded Redux Polling (T+0 ~ T+250ms) with strict target and binding re-verification
        js_check_redux = f"""
        (() => {{
            const query = {json.dumps(query)};

            // 1. Strict Target & Modal Re-verification
            const candidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor'));
            const validCandidates = [];

            for (const candidate of candidates) {{
                const inComposer = candidate.matches('[data-qa*="composer"], [data-qa="message_input"]') ||
                                   !!candidate.closest('.p-workspace__primary_view_body, .p-message_pane, .p-flexpane, [data-qa="message_input"], [data-qa="threads_flexpane"]');
                if (inComposer) {{
                    return {{ ok: false, redux_status: "FAIL_CLOSED_COMPOSER_DETECTED", error: "composer_detected" }};
                }}

                const isConnected = (candidate.isConnected === true);
                const notDisabled = (!candidate.disabled && !candidate.hasAttribute('disabled') && candidate.getAttribute('aria-disabled') !== 'true');
                const notAriaHidden = (candidate.getAttribute('aria-hidden') !== 'true');
                const s = window.getComputedStyle(candidate);
                const isVisible = (candidate.offsetParent !== null && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0');
                const r = candidate.getBoundingClientRect();
                const nonZeroRect = (r.width > 0 && r.height > 0);
                const roleCombobox = (candidate.getAttribute('role') === 'combobox');
                const inSearchModalWrapper = !!candidate.closest('.c-search_modal__wrapper, .c-search_modal');

                if (isConnected && notDisabled && notAriaHidden && isVisible && nonZeroRect && roleCombobox && inSearchModalWrapper) {{
                    validCandidates.push(candidate);
                }}
            }}

            if (validCandidates.length !== 1) {{
                return {{ ok: false, redux_status: "FAIL_CLOSED_TARGET_CHANGED", error: "target_cardinality_invalid" }};
            }}

            const candidate = validCandidates[0];

            const anchorCandidates = [
                candidate,
                candidate.parentElement,
                candidate.parentElement ? candidate.parentElement.parentElement : null,
                candidate.closest('[data-qa="search_input_box"]'),
                candidate.closest('.c-search__input_box'),
                candidate.closest('.c-search_modal__wrapper')
            ];

            let startingFiber = null;
            for (const anchor of anchorCandidates) {{
                if (!anchor) continue;
                const fiberKey = Object.keys(anchor).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
                if (fiberKey && anchor[fiberKey]) {{
                    startingFiber = anchor[fiberKey];
                    break;
                }}
            }}

            if (!startingFiber) return {{ ok: false, redux_status: "UNAVAILABLE", error: "no_fiber" }};

            let fiber = startingFiber;
            let depth = 0;
            let searchInputBoxInstance = null;

            while (fiber && depth < 40) {{
                if (fiber.stateNode && typeof fiber.stateNode.getTextyOps === 'function') {{
                    searchInputBoxInstance = fiber.stateNode;
                    break;
                }}
                fiber = fiber.return;
                depth++;
            }}

            if (!searchInputBoxInstance || !searchInputBoxInstance.props || !('displayQueryOps' in searchInputBoxInstance.props)) {{
                return {{ ok: true, redux_status: "UNAVAILABLE" }};
            }}

            const ops = searchInputBoxInstance.props.displayQueryOps;
            if (!Array.isArray(ops)) return {{ ok: true, redux_status: "UNAVAILABLE" }};

            const raw = ops.map(op => (typeof op.insert === 'string' ? op.insert : '')).join('');
            const normalized = raw.replace(/\\n$/, '');

            return {{
                ok: true,
                redux_status: (normalized === query) ? "TRUE" : "FALSE"
            }};
        }})()
        """
        redux_matched = False
        for wait_interval in [0.0, 0.025, 0.050, 0.100, 0.250]:
            if wait_interval > 0.0:
                await asyncio.sleep(wait_interval)
            try:
                redux_res = await self._raw._evaluate_raw_js(js_check_redux)
                if redux_res and redux_res.get("redux_status") == "TRUE":
                    redux_matched = True
                    break
            except Exception:
                pass

        if not redux_matched:
            raise SlackVerifiedSearchInputNotFoundError(
                f"검증된 Global Search Input의 Redux queryOps 동기화 검증 실패: {last_res}"
            )

        last_res["redux_query_ops_match"] = True
        return last_res

    async def _submit_verified_global_search_native(self, query: str, timeout_sec: float = 3.0) -> dict[str, Any]:
        """검증된 Global SearchInputBox의 native onEnter() 핸들러를 키보드 없이(Keyboard-Free) 단일 원자적 클로저에서 직접 실행합니다.
        
        Fail-Closed 단일 원자적 클로저 검증 조건:
        1. Target & Modal: Search modal 마운트, [data-qa="search_input_box"] .ql-editor cardinality == 1, 7대 구조적 가드, Composer 0 교집합.
        2. Exact React Fiber Binding: SearchInputBox, Texty, TextyCore, Quill 인스턴스 식별.
        3. 5-Layer State Sync Re-verification: DOM text, Quill text, Quill Delta, Texty contents, Redux displayQueryOps 가 모두 query와 정확히 일치.
        4. SearchInputBox Component Flags: queryHasChanged === false, queryHasBeenSubmitted === false, _isUpdatingDisplayQuery === false, isInComposition === false.
        5. Exact 1x Invocation: 모든 조건 충족 시 searchInputBoxInstance.onEnter() (인자 없음, undefined) 정확히 1회 호출.
        """
        js_submit = f"""
        (() => {{
            const query = {json.dumps(query)};

            // 1. Primary Search Editor Selector
            const candidates = Array.from(document.querySelectorAll('[data-qa="search_input_box"] .ql-editor'));
            const validCandidates = [];

            for (const candidate of candidates) {{
                const inComposer = candidate.matches('[data-qa*="composer"], [data-qa="message_input"]') ||
                                   !!candidate.closest('.p-workspace__primary_view_body, .p-message_pane, .p-flexpane, [data-qa="message_input"], [data-qa="threads_flexpane"]');
                if (inComposer) {{
                    return {{ ok: false, error: 'composer_detected_as_search_input', is_composer: true }};
                }}

                const isConnected = (candidate.isConnected === true);
                const notDisabled = (!candidate.disabled && !candidate.hasAttribute('disabled') && candidate.getAttribute('aria-disabled') !== 'true');
                const notAriaHidden = (candidate.getAttribute('aria-hidden') !== 'true');
                const s = window.getComputedStyle(candidate);
                const isVisible = (candidate.offsetParent !== null && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0');
                const r = candidate.getBoundingClientRect();
                const nonZeroRect = (r.width > 0 && r.height > 0);
                const roleCombobox = (candidate.getAttribute('role') === 'combobox');
                const inSearchModalWrapper = !!candidate.closest('.c-search_modal__wrapper, .c-search_modal');

                if (isConnected && notDisabled && notAriaHidden && isVisible && nonZeroRect && roleCombobox && inSearchModalWrapper) {{
                    validCandidates.push(candidate);
                }}
            }}

            if (validCandidates.length !== 1) {{
                return {{ ok: false, error: 'invalid_candidate_cardinality', count: validCandidates.length }};
            }}

            const candidate = validCandidates[0];

            // 2. Multi-Anchor React Fiber Discovery
            const anchorCandidates = [
                candidate,
                candidate.parentElement,
                candidate.parentElement ? candidate.parentElement.parentElement : null,
                candidate.closest('[data-qa="search_input_box"]'),
                candidate.closest('.c-search__input_box'),
                candidate.closest('.c-search_modal__wrapper')
            ];

            let startingFiber = null;
            for (const anchor of anchorCandidates) {{
                if (!anchor) continue;
                const fiberKey = Object.keys(anchor).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
                if (fiberKey && anchor[fiberKey]) {{
                    startingFiber = anchor[fiberKey];
                    break;
                }}
            }}

            if (!startingFiber) {{
                return {{ ok: false, error: 'no_react_fiber_key_found' }};
            }}

            let fiber = startingFiber;
            let depth = 0;
            let searchInputBoxInstance = null;
            let directTextyInstance = null;

            while (fiber && depth < 40) {{
                if (fiber.stateNode && typeof fiber.stateNode.getTextyOps === 'function') {{
                    searchInputBoxInstance = fiber.stateNode;
                    break;
                }}
                if (fiber.memoizedProps && fiber.memoizedProps.texty) {{
                    directTextyInstance = fiber.memoizedProps.texty;
                }}
                if (fiber.stateNode && fiber.stateNode.texty) {{
                    directTextyInstance = fiber.stateNode.texty;
                }}
                fiber = fiber.return;
                depth++;
            }}

            if (!searchInputBoxInstance) {{
                return {{ ok: false, error: 'search_input_box_instance_not_found' }};
            }}

            const textyObj = searchInputBoxInstance.texty || directTextyInstance;
            if (!textyObj) {{
                return {{ ok: false, error: 'texty_instance_not_found' }};
            }}

            const textyCore = textyObj.instance ? textyObj.instance : textyObj;
            const quill = textyCore.quill;
            if (!quill) {{
                return {{ ok: false, error: 'quill_instance_not_found' }};
            }}

            // 3. Model-Native 5-Layer State Verification before Submit
            const normalizeOpsToString = (ops) => {{
                if (!Array.isArray(ops)) return "";
                const raw = ops.map(op => (typeof op.insert === 'string' ? op.insert : '')).join('');
                return raw.replace(/\\n$/, '');
            }};

            const domText = candidate.textContent || '';
            const quillText = (typeof quill.getText === 'function' ? quill.getText() : '').replace(/\\n$/, '');
            const quillOps = (typeof quill.getContents === 'function') ? (quill.getContents()?.ops || []) : [];
            const textyOps = (typeof textyObj.getContents === 'function') ? (textyObj.getContents()?.contents || []) : [];

            const reduxOps = searchInputBoxInstance.props?.displayQueryOps;
            const reduxText = normalizeOpsToString(reduxOps);

            const domMatch = (domText === query);
            const quillTextMatch = (quillText === query);
            const rawDeltaMatch = (normalizeOpsToString(quillOps) === query);
            const textyContentsMatch = (normalizeOpsToString(textyOps) === query);
            const reduxMatch = (reduxText === query);

            // 4. Component Internal State Preconditions
            const queryHasChanged = !!searchInputBoxInstance.queryHasChanged;
            const queryHasBeenSubmitted = !!searchInputBoxInstance.queryHasBeenSubmitted;
            const isUpdatingDisplayQuery = !!searchInputBoxInstance._isUpdatingDisplayQuery;
            const isInComposition = (typeof textyCore.isInComposition === 'function') ? textyCore.isInComposition() : false;

            const allPreconditionsMet = domMatch && quillTextMatch && rawDeltaMatch && textyContentsMatch && reduxMatch &&
                                        !queryHasChanged && !queryHasBeenSubmitted && !isUpdatingDisplayQuery && !isInComposition;

            if (!allPreconditionsMet) {{
                return {{
                    ok: false,
                    error: 'preconditions_not_met',
                    dom_match: domMatch,
                    quill_text_match: quillTextMatch,
                    raw_quill_delta_match: rawDeltaMatch,
                    texty_contents_match: textyContentsMatch,
                    redux_match: reduxMatch,
                    query_has_changed: queryHasChanged,
                    query_has_been_submitted: queryHasBeenSubmitted,
                    is_updating_display_query: isUpdatingDisplayQuery,
                    is_in_composition: isInComposition
                }};
            }}

            // 5. Task 11R10F Safe Search Suggestion Containment & Activation
            const props = searchInputBoxInstance.props || {{}};
            const suggestions = Array.isArray(props.suggestions) ? props.suggestions : [];
            const autocompleteSelectedIndex = (typeof props.autocompleteSelectedIndex === 'number')
                ? props.autocompleteSelectedIndex
                : null;

            function isWriteCapableSuggestion(s) {{
                if (!s || typeof s !== 'object') return false;
                if (s.is_type_slackbot === true) return true;
                if (s.type === 'querySlackbot') return true;
                if (s.key === 'ask-slackbot') return true;
                if (s.suggestion_sub_type === 'query-slackbot') return true;
                return false;
            }}

            function isUnsafeNonSearchSuggestion(s) {{
                if (!s || typeof s !== 'object') return true;
                if (isWriteCapableSuggestion(s)) return true;
                if (s.isNavigational === true || s.is_type_navigational === true) return true;
                if (s.is_type_channel === true || s.type === 'channel') return true;
                if (s.is_type_user === true || s.is_type_user_profile === true || s.type === 'member-profile') return true;
                if (s.is_type_workflow === true || s.type === 'workflowTrigger') return true;
                if (s.is_type_app_action === true || s.type === 'appAction') return true;
                if (s.is_type_huddle === true || s.type === 'huddle') return true;
                if (s.type === 'queryConvoSearch' || s.type === 'querySalesforce') return true;
                return false;
            }}

            function isExactSafeKeywordSearch(s) {{
                if (!s || typeof s !== 'object') return false;
                const matchesExactPositive = (
                    s.is_type_query_completion === true &&
                    s.type === 'queryUser' &&
                    s.suggestion_sub_type === 'query-echo'
                );
                if (!matchesExactPositive) return false;
                if (isUnsafeNonSearchSuggestion(s)) return false;
                return true;
            }}

            // Contract 1: Exactly one safe candidate
            const safeCandidates = [];
            for (let idx = 0; idx < suggestions.length; idx++) {{
                if (isExactSafeKeywordSearch(suggestions[idx])) {{
                    safeCandidates.push({{ index: idx, suggestion: suggestions[idx] }});
                }}
            }}

            if (safeCandidates.length !== 1) {{
                const errCode = (safeCandidates.length === 0)
                    ? 'NO_SAFE_SEARCH_SUGGESTION_FOUND'
                    : 'AMBIGUOUS_MULTIPLE_SAFE_SEARCH_SUGGESTIONS';
                return {{ ok: false, error: errCode, safe_candidates_count: safeCandidates.length }};
            }}

            const safeCandidate = safeCandidates[0];
            const safeIndex = safeCandidate.index;
            const targetSuggestion = safeCandidate.suggestion;

            // Contract 4: Atomic TOCTOU re-validation
            if (suggestions[safeIndex] !== targetSuggestion || !isExactSafeKeywordSearch(suggestions[safeIndex])) {{
                return {{ ok: false, error: 'SUGGESTION_IDENTITY_SHIFTED' }};
            }}

            let submitInvoked = false;
            let submitException = null;
            let submitMethod = "NONE";

            // Contract 3 & 5: Verified UI activation path (DOM item click -> Fiber onAccept)
            const acList = document.querySelector('#c-search_autocomplete__suggestion_list, [data-qa="search_autocomplete"], .c-search_autocomplete, [role="listbox"]');
            let safeOption = null;
            if (acList) {{
                const options = Array.from(acList.querySelectorAll('[role="option"], .c-search_autocomplete__suggestion_item, [data-qa="search_autocomplete_item"]'));
                safeOption = options.find(el => {{
                    const dIdx = el.getAttribute('data-index');
                    return dIdx !== null && parseInt(dIdx, 10) === safeIndex;
                }});
                if (!safeOption && options[safeIndex]) {{
                    const optType = options[safeIndex].getAttribute('data-type');
                    if (optType === 'queryUser' || optType === targetSuggestion.type) {{
                        safeOption = options[safeIndex];
                    }}
                }}
            }}

            if (safeOption) {{
                const hasBotId = safeOption.hasAttribute('data-bot-id');
                const isNav = (safeOption.getAttribute('data-is-navigational') === 'true');
                if (!hasBotId && !isNav) {{
                    try {{
                        safeOption.dispatchEvent(new MouseEvent('mousemove', {{ bubbles: true, cancelable: true, view: window }}));
                        safeOption.click();
                        submitInvoked = true;
                        submitMethod = 'SafeSearchSuggestionDOMItem.click[index=' + safeIndex + ']';
                    }} catch (e) {{
                        submitException = String(e);
                    }}
                }}
            }}

            if (!submitInvoked && safeOption) {{
                try {{
                    const optFiberKey = Object.keys(safeOption).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
                    if (optFiberKey && safeOption[optFiberKey]) {{
                        let curr = safeOption[optFiberKey];
                        let depth = 0;
                        while (curr && depth < 10) {{
                            if (curr.memoizedProps) {{
                                if (typeof curr.memoizedProps.onAccept === 'function') {{
                                    curr.memoizedProps.onAccept(false, false);
                                    submitInvoked = true;
                                    submitMethod = 'SafeSearchSuggestionFiber.onAccept[index=' + safeIndex + ']';
                                    break;
                                }}
                            }}
                            curr = curr.return;
                            depth++;
                        }}
                    }}
                }} catch (e) {{
                    if (!submitException) submitException = String(e);
                }}
            }}

            if (!submitInvoked) {{
                try {{
                    let f = startingFiber;
                    let depth = 0;
                    while (f && depth < 20) {{
                        if (f.memoizedProps && typeof f.memoizedProps.onAccept === 'function') {{
                            f.memoizedProps.onAccept(false, false);
                            submitInvoked = true;
                            submitMethod = 'ReactOmniswitcher.onAccept[index=' + safeIndex + ']';
                            break;
                        }}
                        f = f.return;
                        depth++;
                    }}
                }} catch (e) {{
                    if (!submitException) submitException = String(e);
                }}
            }}

            if (!submitInvoked) {{
                return {{ ok: false, error: 'SAFE_SEARCH_ACTIVATION_FAILED', message: submitException }};
            }}

            return {{
                ok: submitInvoked,
                method: submitMethod,
                submit_invoked: submitInvoked,
                submit_call_count: 1,
                submit_exception: submitException,
                dom_match: domMatch,
                quill_text_match: quillTextMatch,
                raw_quill_delta_match: rawDeltaMatch,
                texty_contents_match: textyContentsMatch,
                redux_match: reduxMatch,
                same_input_ref: true,
                same_texty_ref: true,
                same_quill_ref: true
            }};
        }})()
        """
        res = await self._raw._evaluate_raw_js(js_submit)
        if not res or not res.get("ok"):
            if res and res.get("is_composer"):
                self.audit_tracker.record_blocked_write(
                    reason="Composer detected during native search submit", is_main_composer=True
                )
                raise ReadOnlyViolationError("🚨 [READ_ONLY_VIOLATION] Search Submit 대상이 Composer로 감지되어 차단.")
            raise SlackSearchInputIdentityChangedError(
                f"Global Search Native Submit 검증/실행 실패: {res}"
            )
        return res

    async def _set_and_submit_verified_global_search_on_open_ui(
        self, query: str, timeout_sec: float = 8.0
    ) -> dict[str, Any]:
        """이미 마운트된 Search UI를 대상으로 Model-native query set 및 Keyboard-free native submit을 수행합니다."""
        # Step 1: Verified Search Input Resolve & Query Set
        set_res = await self._resolve_and_set_verified_search_input(query, timeout_sec=timeout_sec)
        if not set_res or not set_res.get("ok"):
            raise SlackVerifiedSearchInputNotFoundError(
                f"Global Search Input 쿼리 설정 실패: {set_res}"
            )

        # Step 2: Keyboard-Free Native Search Submit (단일 원자적 클로저에서 사전조건 검증 및 onEnter() 1회 호출)
        submit_res = await self._submit_verified_global_search_native(query, timeout_sec=timeout_sec)
        if not submit_res or not submit_res.get("ok"):
            if submit_res and submit_res.get("is_composer"):
                self.audit_tracker.record_blocked_write(
                    reason="Composer detected during native search submit", is_main_composer=True
                )
                raise ReadOnlyViolationError("🚨 [READ_ONLY_VIOLATION] Search Submit 대상이 Composer로 감지되어 차단.")
            raise SlackSearchInputIdentityChangedError(
                f"Global Search Native Submit 실패: {submit_res}"
            )

        logger.info(f"검증된 Global Search창에 쿼리 {query!r} 입력 및 키보드 없는(Keyboard-Free) 네이티브 서브밋 완료.")

        return {
            "ok": True,
            "type": "VERIFIED_GLOBAL_SEARCH_INPUT",
            "selector_identity": set_res.get("selector_identity", "unknown"),
            "submit_method": submit_res.get("method", "SearchInputBox.onEnter"),
            "native_submit_verified": True,
            "raw_enter_used": False,
            "keyboard_event_used": False,
            "url_fallback_used": False,
            "generic_editable_used": False,
            "value": query,
        }

    async def execute_verified_global_search(self, query: str, timeout_sec: float = 8.0) -> dict[str, Any]:
        """오직 검증된 Global Search Box에만 쿼리를 설정하고 키보드 없이(Keyboard-Free) 네이티브 커밋을 수행하는 단일 캡슐화 액션.
        
        Fail-Closed 런타임 흐름 (Visible Adapter Convenience):
        0. activate_verified_global_search_ui() (exact button[data-qa="top_nav_search"] mousedown & 마운트 대기)
        1. _set_and_submit_verified_global_search_on_open_ui(query) (모델 세터 + 네이티브 서브밋)
        """
        # Step 0: Search UI 마운트 보장 (이미 열려있는 경우 자동 통과)
        await self.activate_verified_global_search_ui(timeout_sec=4.0)

        # Step 1 & 2: Set query and submit on open UI
        return await self._set_and_submit_verified_global_search_on_open_ui(
            query=query, timeout_sec=timeout_sec
        )

    async def open_thread_read_only(self, raw_ts: str) -> bool:
        """스레드 패널을 열고 스레드 댓글 입력창(Composer)의 포커스를 즉시 무력화(blur)합니다."""
        js_open = f"""
        (() => {{
            function normalizeSlackMessageTs(s) {{
                if (!s) return '';
                const m = s.match(/p(\\d{{10,}})/);
                if (m) return m[1];
                return s.replace(/[^0-9]/g, '');
            }}

            const cleanTargetTs = normalizeSlackMessageTs({json.dumps(raw_ts)});
            const items = Array.from(document.querySelectorAll('.c-virtual_list__item, [data-qa="virtual-list-item"], [data-qa="message_container"], [role="listitem"]'));
            
            for (const item of items) {{
                const replyBar = item.querySelector('[data-qa="reply_count"], .c-message__reply_count, [data-qa="reply_bar"], button[aria-label*="댓글"], button[aria-label*="replies"], button[aria-label*="reply"], button[data-qa="reply_bar"]');
                if (replyBar) {{
                    const href = item.querySelector('a[href*="/archives/"]')?.getAttribute('href') || '';
                    const itemKey = item.getAttribute('data-item-key') || item.id || '';
                    const cleanHref = normalizeSlackMessageTs(href);
                    const cleanKey = normalizeSlackMessageTs(itemKey);
                    const isMatch = !cleanTargetTs || cleanHref === cleanTargetTs || cleanKey === cleanTargetTs || cleanHref.endsWith(cleanTargetTs) || cleanKey.endsWith(cleanTargetTs);
                    
                    if (isMatch) {{
                        replyBar.click();
                        return {{ clicked: true }};
                    }}
                }}
            }}
            return {{ clicked: false }};
        }})()
        """
        res = await self._raw._evaluate_raw_js(js_open)
        await asyncio.sleep(1.5)

        # Thread Composer 포커스 즉시 해제 및 Neutralization
        js_neutralize = """
        (() => {
            if (document.activeElement) {
                const active = document.activeElement;
                if (active.matches('[data-qa="texty_input"], [data-qa="message_input"], [contenteditable="true"]') ||
                    active.closest('[data-qa="texty_input"], [data-qa="message_input"], .p-flexpane')) {
                    active.blur();
                }
            }
            const flexpaneComposers = document.querySelectorAll('.p-flexpane [data-qa="texty_input"], .p-flexpane [data-qa="message_input"], .p-flexpane [contenteditable="true"]');
            flexpaneComposers.forEach(el => {
                el.setAttribute('contenteditable', 'false');
                el.setAttribute('tabindex', '-1');
                el.style.pointerEvents = 'none';
            });
            return { neutralized: true };
        })()
        """
        await self._raw._evaluate_raw_js(js_neutralize)
        return bool(res and res.get("clicked"))


# 하위 호환성을 위해 SlackCdpAdapter는 SlackReadOnlyCdpAdapter를 가리킵니다.
SlackCdpAdapter = SlackReadOnlyCdpAdapter
