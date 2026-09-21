"""Slack Read-Only Safety Barrier & Action Validator (MVP 3.4.1).

구조적 Slack Read-Only 강제를 위한 계층:
1. Typed Read-Only Capability Allowlist
2. Composer & Send Control Blocklist
3. DOM Capture-Phase Guard Injection
4. Strict Selector & Active Element Verification
"""

from __future__ import annotations

import enum
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SlackReadCapability(str, enum.Enum):
    """Production Agent가 호출 가능한 엄격한 Read-Only 액션 허용 목록."""

    READ_DOM = "READ_DOM"
    READ_MESSAGE_LIST = "READ_MESSAGE_LIST"
    NAVIGATE_CHANNEL = "NAVIGATE_CHANNEL"
    NAVIGATE_PERMALINK = "NAVIGATE_PERMALINK"
    OPEN_SEARCH = "OPEN_SEARCH"
    SET_SEARCH_QUERY = "SET_SEARCH_QUERY"
    EXECUTE_SEARCH = "EXECUTE_SEARCH"
    SCROLL_MESSAGE_LIST = "SCROLL_MESSAGE_LIST"
    OPEN_THREAD_READ_ONLY = "OPEN_THREAD_READ_ONLY"
    READ_THREAD = "READ_THREAD"


class BootstrapCapability(str, enum.Enum):
    """Bootstrap 전용 제한적 Capability.

    Runtime Evidence Collection에서 사용이 금지됩니다.
    제약: composer 접근 금지, text 입력 금지, keyboard 입력 금지, Slack write action 금지.
    Sidebar Ctrl+Click의 mouse modifier만 허용됩니다.
    """

    READ_MAIN_STATE = "READ_MAIN_STATE"
    OPEN_SECONDARY_WINDOW = "OPEN_SECONDARY_WINDOW"
    VERIFY_MAIN_PRESERVED = "VERIFY_MAIN_PRESERVED"


class SlackSecurityViolationError(Exception):
    """Slack 보안 정책 위반 기본 예외."""


class ReadOnlyViolationError(SlackSecurityViolationError):
    """Slack 쓰기/전송/수정/삭제 등 금지된 작업 시도 시 발생하는 예외."""


class SearchInputNotVerifiedError(SlackSecurityViolationError):
    """검색창이 명확하게 검증되지 않았거나 Composer로 오인될 위험이 있을 때 발생하는 예외."""


class SlackVerifiedSearchInputNotFoundError(SearchInputNotVerifiedError):
    """검증된 Global Search Input을 찾지 못했을 때 발생하는 Fail-Closed 예외."""


class SlackSearchInputIdentityChangedError(SlackSecurityViolationError):
    """Enter 전송 직전 Search Input의 Identity 또는 활성 상태가 변조되었을 때 발생하는 Fail-Closed 예외 (TOCTOU 방어)."""


class ProhibitedActionError(SlackSecurityViolationError):
    """허용되지 않은 Action 또는 Selector 사용 시 발생하는 예외."""


# ==============================================================================
# Selector Allowlist & Blocklist
# ==============================================================================

# 클릭 가능한 검증된 Production Selector 목록
ALLOWED_CLICK_SELECTORS = frozenset([
    '[data-qa="top_nav_search"]',
    'button[data-qa="top_nav_search"]',
    '.p-top_nav__search',
    '[data-qa="reply_count"]',
    '[data-qa="reply_bar"]',
    'button[data-qa="reply_bar"]',
    '.c-message__reply_count',
    'button[aria-label*="댓글"]',
    'button[aria-label*="replies"]',
    'button[aria-label*="reply"]',
    '[data-qa="top_nav_search_clear"]',
])

# 절대 클릭/접근/입력/Enter 전송이 금지된 Blocked Selector 패턴
PROHIBITED_ELEMENT_PATTERNS = frozenset([
    'texty_input',
    'message_input',
    'composer',
    'texty_send_button',
    'send_button',
    'send-button',
    'send',
    '전송',
    'submit',
    'primary_view_body',
    'thread_reply_composer',
    'reaction',
    'add_reaction',
    'action_menu',
    'more_actions',
    'bookmark',
    'pin_message',
    'delete_message',
    'edit_message',
    'file_upload',
    'workflow',
    'form',
])

# 검증된 Global Search Input Selector 목록 (Composer 절대 제외)
VERIFIED_GLOBAL_SEARCH_SELECTORS = (
    '[data-qa="search_input_box"] .ql-editor',
    '[data-qa="search_input_box"] [contenteditable="true"]',
    'input[data-qa="top_nav_search__input"]',
)


class SlackActionValidator:
    """Slack Read-Only Action 및 Selector 유효성 검증기."""

    @staticmethod
    def validate_capability(capability: SlackReadCapability) -> None:
        """지정된 capability가 Read-Only allowlist에 존재하는지 검증."""
        if not isinstance(capability, SlackReadCapability):
            raise ReadOnlyViolationError(
                f"🚨 [READ_ONLY_VIOLATION] 허용되지 않은 비인가 작업 시도: {capability}"
            )

    @staticmethod
    def validate_click_selector(selector: str) -> None:
        """클릭 대상 selector가 allowlist에 있고 composer/send 계열이 아님을 검증."""
        clean_sel = selector.strip().lower()

        for blocked in PROHIBITED_ELEMENT_PATTERNS:
            if blocked in clean_sel:
                raise ReadOnlyViolationError(
                    f"🚨 [READ_ONLY_VIOLATION] 금지된 요소({blocked})에 대한 클릭 시도 차단: selector='{selector}'"
                )

        is_allowed = any(
            allowed in selector or selector in allowed for allowed in ALLOWED_CLICK_SELECTORS
        )
        if not is_allowed:
            raise ProhibitedActionError(
                f"🚨 [PROHIBITED_ACTION] Allowlist에 등록되지 않은 임의 Selector 클릭 차단: selector='{selector}'"
            )



