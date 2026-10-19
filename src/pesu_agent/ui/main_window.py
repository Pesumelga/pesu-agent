"""PESU Agent Product Main Window (Phase 10.1).

Standard activating WinForms Form with embedded WebView2 for Product Shell.
Completely decoupled from hidden background Slack host (PesuNonActivatingForm).

Architecture Invariants:
1. Normal Activating Window:
   - PRODUCT_MAIN_WINDOW_USES_NOACTIVATE = False
   - PRODUCT_MAIN_WINDOW_USES_TOOLWINDOW = False
   - Form shows, activates, and brings to foreground on user request.
2. Isolated UI Profile:
   - %LOCALAPPDATA%\\PESU\\ui-runtime
   - Completely independent from %LOCALAPPDATA%\\PESU\\slack-runtime-direct.
3. Close-to-Tray Lifecycle:
   - User clicking [X] triggers FormClosing.
   - If not explicitly exiting: e.Cancel = True, form.Hide().
   - Window remains alive in memory; process stays running in System Tray.
4. Clean Teardown:
   - When explicitly exiting (Tray -> Exit): _is_exiting = True, full dispose.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Optional

from pesu_agent.runtime.resource_resolver import get_bundle_root, is_frozen
from pesu_agent.ui.investigation_service import InvestigationService
from pesu_agent.ui.messages import UiMessage
from pesu_agent.ui.preferences import ProductPreferencesStore
from pesu_agent.ui.webview_bridge import ProductWebViewBridge

logger = logging.getLogger(__name__)

PRODUCT_MAIN_WINDOW_USES_NOACTIVATE: bool = False
PRODUCT_MAIN_WINDOW_USES_TOOLWINDOW: bool = False

UI_RUNTIME_PROFILE_PATH: str = str(
    Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "PESU" / "ui-runtime"
)


def get_ui_assets_dir() -> Path:
    """Resolves the Product UI static assets directory in both dev and frozen environments."""
    # 1. Frozen PyInstaller mode
    if is_frozen():
        bundle_root = get_bundle_root()
        candidates = [
            bundle_root / "pesu_agent" / "ui" / "assets",
            bundle_root / "ui" / "assets",
            bundle_root / "assets",
        ]
        for candidate in candidates:
            if candidate.exists() and (candidate / "index.html").exists():
                logger.info(f"Resolved frozen Product UI assets at: {candidate}")
                return candidate.resolve()

    # 2. Development mode: src/pesu_agent/ui/assets
    dev_path = Path(__file__).parent / "assets"
    if dev_path.exists() and (dev_path / "index.html").exists():
        return dev_path.resolve()

    return dev_path.resolve()

PLACEHOLDER_HTML: str = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>PESU Agent</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0;
      padding: 40px;
      background: #0f172a;
      color: #f8fafc;
    }
    h1 {
      margin: 0 0 8px 0;
      font-size: 26px;
      font-weight: 700;
      color: #38bdf8;
    }
    p {
      margin: 0 0 16px 0;
      color: #94a3b8;
      font-size: 14px;
    }
    .badge {
      display: inline-block;
      padding: 4px 12px;
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 9999px;
      font-size: 12px;
      color: #cbd5e1;
    }
  </style>
</head>
<body>
  <h1>PESU Agent</h1>
  <p>Product Shell &middot; Phase 10.1</p>
  <span class="badge">Ready &middot; Close window to minimize to System Tray</span>
</body>
</html>
"""


class ProductMainWindow:
    """Encapsulates the standard activating WinForms Form containing the Product UI WebView2."""

    def __init__(
        self,
        title: str = "PESU Agent",
        width: int = 980,
        height: int = 740,
        profile_path: Optional[str] = None,
        allow_real_slack_external_analysis: bool = False,
        preferences_store: Optional[ProductPreferencesStore] = None,
        startup_manager: Optional[Any] = None,
    ) -> None:
        self.title = title
        self.width = width
        self.height = height
        self.profile_path = profile_path or UI_RUNTIME_PROFILE_PATH
        self.allow_real_slack_external_analysis = allow_real_slack_external_analysis

        self._form: Any = None
        self._webview: Any = None
        self._is_exiting: bool = False
        self._webview_ready: bool = False
        self.bridge: ProductWebViewBridge = ProductWebViewBridge()
        self.investigation_service: InvestigationService = InvestigationService(
            post_to_ui=lambda msg: self.bridge.send_message(msg),
            allow_real_slack_external_analysis=allow_real_slack_external_analysis,
            preferences_store=preferences_store,
            startup_manager=startup_manager,
        )
        self.bridge._on_query_submit = self._on_query_submit
        self.bridge._on_citation_open = self._on_citation_open
        self.bridge._on_consent_grant = self._on_consent_grant
        self.bridge._on_consent_cancel = self._on_consent_cancel
        self.bridge._on_login_request = self._on_login_request
        self.bridge._on_openrouter_key_save = self._on_openrouter_key_save
        self.bridge._on_openrouter_key_remove = self._on_openrouter_key_remove
        self.bridge._on_openrouter_key_cancel = self._on_openrouter_key_cancel
        self.bridge._on_openrouter_status_get = self._on_openrouter_status_get
        self.bridge._on_status_request = self._on_status_request
        self.bridge._on_onboarding_complete = self._on_onboarding_complete
        self.bridge._on_onboarding_state_get = self._on_onboarding_state_get
        self.bridge._on_autostart_status_get = self._on_autostart_status_get
        self.bridge._on_autostart_enable = self._on_autostart_enable
        self.bridge._on_autostart_disable = self._on_autostart_disable
        self.bridge._on_history_list = self._on_history_list
        self.bridge._on_history_open = self._on_history_open
        self.bridge._on_history_delete = self._on_history_delete
        self.bridge._on_history_clear = self._on_history_clear
        self.bridge._on_history_status_get = self._on_history_status_get
        self.bridge._on_history_exit = self._on_history_exit

    def _on_query_submit(self, query: str, request_id: Optional[str]) -> None:
        """Handles query.submit by delegating to the background investigation worker."""
        self.investigation_service.submit_investigation(query=query, request_id=request_id)

    def _on_citation_open(self, evidence_id: str, request_id: Optional[str]) -> None:
        """Handles citation.open by validating and opening the canonical permalink."""
        success, reason = self.investigation_service.open_citation(evidence_id)
        self.bridge.send_message(
            UiMessage(
                type="citation.open_ack",
                request_id=request_id,
                payload={"evidence_id": evidence_id, "success": success, "reason": reason},
            )
        )

    def _on_consent_grant(self, request_id: Optional[str]) -> None:
        """Handles consent.external_analysis.grant by delegating to investigation_service."""
        self.investigation_service.grant_consent(request_id=request_id)

    def _on_consent_cancel(self, request_id: Optional[str]) -> None:
        """Handles consent.external_analysis.cancel by delegating to investigation_service."""
        self.investigation_service.cancel_consent(request_id=request_id)

    def _on_login_request(self, request_id: Optional[str]) -> None:
        """Handles slack.login.request by delegating to investigation_service."""
        self.investigation_service.start_login_setup(request_id=request_id)

    def _on_openrouter_key_save(self, key: str, request_id: Optional[str]) -> None:
        """Handles provider.openrouter.key.save by delegating to investigation_service."""
        self.investigation_service.save_openrouter_key(key=key, request_id=request_id)

    def _on_openrouter_key_remove(self, request_id: Optional[str]) -> None:
        """Handles provider.openrouter.key.remove by delegating to investigation_service."""
        self.investigation_service.remove_openrouter_key(request_id=request_id)

    def _on_openrouter_key_cancel(self, request_id: Optional[str]) -> None:
        """Handles provider.openrouter.key.cancel by delegating to investigation_service."""
        self.investigation_service.cancel_openrouter_key_setup(request_id=request_id)

    def _on_openrouter_status_get(self) -> None:
        """Handles provider.openrouter.status.get by pushing current credential status."""
        self.investigation_service.push_openrouter_status()

    def _on_status_request(self) -> None:
        """Handles app.status.request by pushing current app, consent, and provider statuses."""
        if self.investigation_service and self.bridge:
            # 1. App status
            self.bridge.send_message(
                UiMessage(
                    type="app.status",
                    payload={"status": self.investigation_service.current_status},
                )
            )
            # 2. Consent status
            consent_state = (
                "GRANTED_FOR_SESSION"
                if self.investigation_service.consent_manager.is_granted()
                else "NOT_GRANTED"
            )
            self.bridge.send_message(
                UiMessage(
                    type="consent.external_analysis.state",
                    payload={"state": consent_state},
                )
            )
            # 3. Provider status
            self.investigation_service.push_openrouter_status()
            # 4. Auto-start status
            self.investigation_service.push_autostart_status()
            # 5. History status
            self.investigation_service.push_history_status()
            # 6. Onboarding check on initial handshake
            self.investigation_service.check_and_show_onboarding()

    def _on_onboarding_complete(self) -> None:
        """Handles onboarding.complete by persisting completion preference."""
        if self.investigation_service:
            self.investigation_service.complete_onboarding()

    def _on_onboarding_state_get(self) -> None:
        """Handles onboarding.state.get by checking and showing onboarding if needed."""
        if self.investigation_service:
            self.investigation_service.check_and_show_onboarding()

    def _on_autostart_status_get(self, request_id: Optional[str] = None) -> None:
        """Handles startup.auto.status.request by pushing current registration state."""
        if self.investigation_service:
            self.investigation_service.push_autostart_status(request_id=request_id)

    def _on_autostart_enable(self, request_id: Optional[str] = None) -> None:
        """Handles startup.auto.enable by registering auto-start in HKCU Run."""
        if self.investigation_service:
            self.investigation_service.enable_autostart(request_id=request_id)

    def _on_autostart_disable(self, request_id: Optional[str] = None) -> None:
        """Handles startup.auto.disable by deleting auto-start in HKCU Run."""
        if self.investigation_service:
            self.investigation_service.disable_autostart(request_id=request_id)

    def _on_history_list(self, limit: int, offset: int, request_id: Optional[str] = None) -> None:
        """Handles history.list by fetching bounded history list."""
        if self.investigation_service:
            self.investigation_service.list_history(limit=limit, offset=offset, request_id=request_id)

    def _on_history_open(self, history_id: str, request_id: Optional[str] = None) -> None:
        """Handles history.open by restoring past investigation record."""
        if self.investigation_service:
            self.investigation_service.open_history_record(history_id=history_id, request_id=request_id)

    def _on_history_delete(self, history_id: str, request_id: Optional[str] = None) -> None:
        """Handles history.delete by deleting individual history record."""
        if self.investigation_service:
            self.investigation_service.delete_history_record(history_id=history_id, request_id=request_id)

    def _on_history_clear(self, request_id: Optional[str] = None) -> None:
        """Handles history.clear by wiping all history records."""
        if self.investigation_service:
            self.investigation_service.clear_history(request_id=request_id)

    def _on_history_status_get(self, request_id: Optional[str] = None) -> None:
        """Handles history.status.get by pushing current history availability and count."""
        if self.investigation_service:
            self.investigation_service.push_history_status(request_id=request_id)

    def _on_history_exit(self) -> None:
        """Handles history.exit by clearing historical citations."""
        if self.investigation_service:
            self.investigation_service.exit_history_view()

    @property
    def form(self) -> Any:
        return self._form

    @property
    def webview(self) -> Any:
        return self._webview

    @property
    def is_visible(self) -> bool:
        return bool(self._form and getattr(self._form, "Visible", False))

    @property
    def is_disposed(self) -> bool:
        if self._form is None:
            return True
        val = getattr(self._form, "IsDisposed", False)
        return val is True

    def create_and_initialize(self) -> Any:
        """Instantiates the Form and attaches the WebView2 control."""
        if self._form is not None:
            return self._form

        import clr
        clr.AddReference("System")
        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")

        from System.Drawing import Point, Size
        import System.Windows.Forms as WinForms
        from Microsoft.Web.WebView2.WinForms import (
            CoreWebView2CreationProperties,
            WebView2,
        )

        Path(self.profile_path).mkdir(parents=True, exist_ok=True)

        # 1. Standard activating Form
        form = WinForms.Form()
        form.Text = self.title
        form.StartPosition = WinForms.FormStartPosition.CenterScreen
        form.Size = Size(self.width, self.height)
        form.MinimumSize = Size(720, 560)

        # 2. Attach close-to-tray handler
        def _on_form_closing(sender, e):
            if not self._is_exiting:
                e.Cancel = True
                self.hide_window()
                logger.info("Main Window [X] clicked -> intercepted: hidden to System Tray.")

        form.FormClosing += _on_form_closing

        # 3. Create and attach WebView2 control
        webview = WebView2()
        props = CoreWebView2CreationProperties()
        props.UserDataFolder = self.profile_path
        webview.CreationProperties = props
        webview.Dock = WinForms.DockStyle.Fill
        form.Controls.Add(webview)
        webview.BringToFront()

        def _on_webview_ready(sender, args):
            try:
                if args.IsSuccess:
                    self._webview_ready = True
                    import clr
                    clr.AddReference("Microsoft.Web.WebView2.Core")
                    from Microsoft.Web.WebView2.Core import CoreWebView2HostResourceAccessKind

                    assets_dir = get_ui_assets_dir()
                    webview.CoreWebView2.SetVirtualHostNameToFolderMapping(
                        "pesu.local",
                        str(assets_dir),
                        CoreWebView2HostResourceAccessKind.Allow,
                    )
                    self.bridge.attach_to_webview(webview, form=form)
                    webview.CoreWebView2.Navigate("https://pesu.local/index.html")
                    logger.info(
                        f"Product UI loaded from https://pesu.local/index.html (mapping: {assets_dir})"
                    )
                else:
                    logger.error(f"Product UI WebView2 init failed: {args.InitializationException}")
            except Exception as ex:
                logger.error(f"Exception during WebView2 ready callback: {ex}", exc_info=True)

        webview.CoreWebView2InitializationCompleted += _on_webview_ready
        webview.EnsureCoreWebView2Async(None)

        self._form = form
        self._webview = webview

        # Force native window handle allocation on the creator (STA) thread
        _ = form.Handle
        form.CreateControl()

        return form

    def show_and_activate(self) -> None:
        """Displays and activates the Main Window in the foreground."""
        if not self._form:
            self.create_and_initialize()

        if getattr(self._form, "InvokeRequired", False) is True:
            import clr
            clr.AddReference("System")
            from System import Action
            self._form.BeginInvoke(Action(self.show_and_activate))
            return

        import System.Windows.Forms as WinForms
        if self._form.WindowState == WinForms.FormWindowState.Minimized:
            self._form.WindowState = WinForms.FormWindowState.Normal

        self._form.Show()
        self._form.Activate()
        self._form.BringToFront()

    def hide_window(self) -> None:
        """Hides the window without disposing it."""
        if self._form and not self.is_disposed:
            if getattr(self._form, "InvokeRequired", False) is True:
                import clr
                clr.AddReference("System")
                from System import Action
                self._form.BeginInvoke(Action(self.hide_window))
                return
            self._form.Hide()

    def dispose_window(self) -> None:
        """Explicitly disposes the WebView2 control and Form during application shutdown."""
        self._is_exiting = True
        if self._form and getattr(self._form, "InvokeRequired", False) is True:
            import clr
            clr.AddReference("System")
            from System import Action
            try:
                self._form.BeginInvoke(Action(self._dispose_internal))
            except Exception:
                self._dispose_internal()
            return
        self._dispose_internal()

    def _dispose_internal(self) -> None:
        if self.investigation_service is not None:
            try:
                self.investigation_service.shutdown()
            except Exception:
                pass
            self.investigation_service = None

        if self._webview is not None:
            try:
                self._webview.Dispose()
            except Exception:
                pass
            self._webview = None

        if self._form is not None:
            try:
                self._form.Close()
                self._form.Dispose()
            except Exception:
                pass
            self._form = None
        logger.info("ProductMainWindow disposed successfully.")
