"""Slack Search Backend Factory & Explicit Composition (Phase 9.9.7a).

Provides typed configuration and a centralized factory for creating SearchBackend
implementations (Desktop CDP, Dedicated WebView2, Dedicated WebView2 Direct Host, RTS API, Synthetic Mock).

Design & Composition Principles:
1. Explicit Backend Selection:
   - Typed enum SlackBackendKind: DESKTOP, WEBVIEW, WEBVIEW_DIRECT, RTS, SYNTHETIC
   - Default is strictly SlackBackendKind.WEBVIEW_DIRECT
   - Configurable via environment variable SLACK_SEARCH_BACKEND or SlackSearchConfig
2. Dependency Injection Boundary:
   - Factory returns abstract SearchBackend Protocol instance
   - High-level orchestrators and evidence collectors depend only on SearchBackend Protocol,
     never on concrete backend implementations
3. Zero Implicit Fallback:
   - If WebView backend fails, it never falls back to Desktop CDP automatically
   - If Desktop backend fails, it never falls back to WebView automatically
   - Errors are propagated upward as typed exceptions
4. Lifecycle & Runtime Ownership:
   - Backend instance scope is long-lived per investigation session (not recreated per search)
   - Start and shutdown ownership belongs to the application bootstrap / session runner
"""

from __future__ import annotations

from enum import Enum
import logging
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from pesu_agent.investigation.search_backend import SearchBackend, SyntheticSearchBackend
from pesu_agent.search.real_slack_backend import RealSlackSearchBackend
from pesu_agent.search.webview_slack_search import (
    WebViewSlackSearchBackend,
    WebViewRuntimeConfig,
)

logger = logging.getLogger(__name__)


class SlackBackendKind(str, Enum):
    """Supported Slack Search Backend variants."""
    DESKTOP = "desktop"
    WEBVIEW = "webview"
    WEBVIEW_DIRECT = "webview_direct"
    RTS = "rts"
    SYNTHETIC = "synthetic"


class SlackSearchConfig(BaseModel):
    """Centralized configuration for Slack Search Backend selection and runtime parameters."""

    backend_kind: SlackBackendKind = Field(
        default_factory=lambda: SlackBackendKind(
            os.getenv("SLACK_SEARCH_BACKEND", "webview_direct").lower()
        ),
        description="Slack Search Backend variant (desktop, webview, webview_direct, rts, synthetic). Default: webview_direct.",
    )
    default_max_results: int = Field(
        default=5,
        description="Default maximum search results to return per query.",
    )
    webview_storage_path: Optional[Path] = Field(
        default=None,
        description="Dedicated WebView profile directory path. If None, default local app data is used.",
    )

    model_config = {
        "use_enum_values": False,
        "arbitrary_types_allowed": True,
    }


PRODUCTION_SELECTABLE_BACKENDS: set[SlackBackendKind] = {
    SlackBackendKind.DESKTOP,
    SlackBackendKind.WEBVIEW,
    SlackBackendKind.WEBVIEW_DIRECT,
}
TEST_INTERNAL_BACKENDS: set[SlackBackendKind] = {
    SlackBackendKind.SYNTHETIC,
}
FROZEN_BACKENDS: set[SlackBackendKind] = {
    SlackBackendKind.RTS,
}


def create_slack_search_backend(
    config: Optional[SlackSearchConfig] = None,
    **kwargs: Any,
) -> SearchBackend:
    """Creates and returns the appropriate SearchBackend instance based on explicit configuration.

    Guarantees:
    - Default backend is WebViewSlackSearchBackend with PesuWebViewHost (webview_direct).
    - Desktop CDP backend is created when explicitly requested (SlackBackendKind.DESKTOP).
    - Dedicated WebView2 backend is created when explicitly requested (SlackBackendKind.WEBVIEW).
    - RTS API backend is frozen and cannot be selected via production configuration.
    - Synthetic backend is test-internal and blocked unless allow_test_backends=True.
    - Zero implicit fallback between backends.
    """
    cfg = config or SlackSearchConfig()
    kind = cfg.backend_kind

    if isinstance(kind, str):
        try:
            kind = SlackBackendKind(kind.lower())
        except ValueError:
            raise ValueError(
                f"Unsupported Slack search backend kind: '{kind}'. "
                f"Supported options: {[b.value for b in SlackBackendKind]}"
            )

    if kind in FROZEN_BACKENDS:
        raise ValueError(
            f"Slack search backend '{kind.value}' is frozen and cannot be selected for execution."
        )

    allow_test = kwargs.pop("allow_test_backends", False)
    if kind in TEST_INTERNAL_BACKENDS and not allow_test:
        raise ValueError(
            f"Slack search backend '{kind.value}' is test-internal and cannot be selected via production configuration."
        )

    logger.info(f"🔧 [SLACK_BACKEND_FACTORY] Initializing search backend kind='{kind.value}'")

    if kind == SlackBackendKind.DESKTOP:
        return RealSlackSearchBackend(
            default_max_results=cfg.default_max_results,
            **kwargs,
        )

    elif kind == SlackBackendKind.WEBVIEW:
        runtime_cfg = WebViewRuntimeConfig(
            storage_path=cfg.webview_storage_path,
        )
        return WebViewSlackSearchBackend(
            config=runtime_cfg,
            default_max_results=cfg.default_max_results,
            **kwargs,
        )

    elif kind == SlackBackendKind.WEBVIEW_DIRECT:
        from pesu_agent.runtime.pesu_webview_host import (
            DIRECT_SLACK_PROFILE_PATH,
            PesuWebViewHost,
        )
        storage_path = cfg.webview_storage_path or Path(DIRECT_SLACK_PROFILE_PATH)
        runtime_cfg = WebViewRuntimeConfig(
            storage_path=Path(storage_path),
        )
        host = kwargs.pop("host", None)
        if host is None:
            host = PesuWebViewHost(
                user_data_folder=str(storage_path),
                initial_url="https://app.slack.com/client",
                title="PESU Dedicated Slack Runtime (Direct)",
            )
        return WebViewSlackSearchBackend(
            config=runtime_cfg,
            default_max_results=cfg.default_max_results,
            host=host,
            **kwargs,
        )

    elif kind == SlackBackendKind.SYNTHETIC:
        return SyntheticSearchBackend(**kwargs)

    else:
        raise ValueError(f"Unhandled Slack backend kind: '{kind}'")
