"""Slack search package."""

from pesu_agent.search.slack_search import (
    SlackSearch,
    SlackSearchResult,
    SlackSearchSession,
    SlackSearchStaleError,
)
from pesu_agent.search.slack_search_resolver import (
    SlackSearchResultResolver,
    VerifiedSlackSearchResult,
    SlackSearchResultResolution,
)
from pesu_agent.search.webview_slack_search import (
    WebViewSlackSearchBackend,
    WebViewSearchError,
    WebViewSearchFailureCode,
    WebViewRuntimeState,
    WebViewRuntimeConfig,
)
from pesu_agent.search.slack_backend_factory import (
    SlackBackendKind,
    SlackSearchConfig,
    create_slack_search_backend,
    PRODUCTION_SELECTABLE_BACKENDS,
    TEST_INTERNAL_BACKENDS,
    FROZEN_BACKENDS,
)
from pesu_agent.search.webview_thread_reader import (
    WebviewThreadReader,
    ThreadReadResult,
    ThreadReadFailureReason,
    is_eligible_thread_candidate,
    classify_message_thread_role,
    normalize_slack_ts,
)

__all__ = [
    "SlackSearch",
    "SlackSearchResult",
    "SlackSearchSession",
    "SlackSearchStaleError",
    "SlackSearchResultResolver",
    "VerifiedSlackSearchResult",
    "SlackSearchResultResolution",
    "WebViewSlackSearchBackend",
    "WebViewSearchError",
    "WebViewSearchFailureCode",
    "WebViewRuntimeState",
    "WebViewRuntimeConfig",
    "SlackBackendKind",
    "SlackSearchConfig",
    "create_slack_search_backend",
    "PRODUCTION_SELECTABLE_BACKENDS",
    "TEST_INTERNAL_BACKENDS",
    "FROZEN_BACKENDS",
    "WebviewThreadReader",
    "ThreadReadResult",
    "ThreadReadFailureReason",
    "is_eligible_thread_candidate",
    "classify_message_thread_role",
    "normalize_slack_ts",
]
