"""PESU Agent System Tray Application (Phase 10.1).

Manages the Windows System Tray NotifyIcon, Main Window single-instance persistent lifecycle,
close-to-tray hide interception, and graceful application exit.

Invariants:
1. Single Persistent Window Instance:
   - PRODUCT_MAIN_WINDOW_CREATE_COUNT_POLICY = "SINGLE_INSTANCE_PERSISTENT"
   - Exactly 1 ProductMainWindow is created per session.
   - Repeated "Open PESU" calls reuse the existing instance.
2. Single Owner Per Slack Profile:
   - ONE_OWNER_PER_SLACK_PROFILE = True
   - Dedicated Slack profile (%LOCALAPPDATA%\\PESU\\slack-runtime-direct) must have at most 1 active owner.
   - In Phase 10.1, Slack Direct Host starts = 0, Login Setup is disabled.
3. Explicit Exit:
   - Only Tray -> Exit performs application termination and resource disposal.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import threading
from typing import Any, Optional

from pesu_agent.runtime.resource_resolver import setup_webview2_runtime_environment
from pesu_agent.ui.main_window import ProductMainWindow

logger = logging.getLogger(__name__)

ONE_OWNER_PER_SLACK_PROFILE: bool = True
PRODUCT_MAIN_WINDOW_CREATE_COUNT_POLICY: str = "SINGLE_INSTANCE_PERSISTENT"


class PesuTrayApp:
    """System Tray Application Controller for PESU Product Shell."""

    def __init__(
        self,
        main_window: Optional[ProductMainWindow] = None,
        allow_real_slack_external_analysis: bool = False,
        preferences_store: Optional[Any] = None,
        startup_manager: Optional[Any] = None,
    ) -> None:
        self._main_window: Optional[ProductMainWindow] = main_window
        self.allow_real_slack_external_analysis = allow_real_slack_external_analysis
        self.preferences_store = preferences_store
        self.startup_manager = startup_manager
        self._notify_icon: Any = None
        self._context_menu: Any = None
        self._context: Any = None
        self._is_exiting: bool = False
        self.window_create_count: int = 1 if main_window is not None else 0
        self._sta_thread: Optional[Any] = None
        self._exit_event: threading.Event = threading.Event()

    @property
    def sta_thread(self) -> Optional[Any]:
        return self._sta_thread

    @property
    def main_window(self) -> Optional[ProductMainWindow]:
        return self._main_window

    @property
    def notify_icon(self) -> Any:
        return self._notify_icon

    @property
    def is_exiting(self) -> bool:
        return self._is_exiting

    def setup_ui(self) -> None:
        """Initializes WinForms components, Tray Icon, and single persistent Main Window."""
        setup_webview2_runtime_environment()

        import clr
        clr.AddReference("System")
        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")

        import System.Windows.Forms as WinForms
        from System.Drawing import Font, FontStyle, SystemIcons

        # 1. Initialize persistent Main Window (Create count = 1)
        if self._main_window is None:
            self._main_window = ProductMainWindow(
                allow_real_slack_external_analysis=self.allow_real_slack_external_analysis,
                preferences_store=self.preferences_store,
                startup_manager=self.startup_manager,
            )
            self.window_create_count += 1
        self._main_window.create_and_initialize()

        # 2. Build ContextMenuStrip for Tray Icon
        menu = WinForms.ContextMenuStrip()
        self._context_menu = menu

        # Item 1: Open PESU (Default)
        item_open = WinForms.ToolStripMenuItem("PESU 열기")
        item_open.Font = Font(item_open.Font, FontStyle.Bold)

        def _on_open_click(sender, e):
            self.open_main_window()

        item_open.Click += _on_open_click
        menu.Items.Add(item_open)

        # Item 2: Status indicator (Disabled informational)
        item_status = WinForms.ToolStripMenuItem("Status: Ready")
        item_status.Enabled = False
        self._item_status = item_status
        menu.Items.Add(item_status)

        # Separator
        menu.Items.Add(WinForms.ToolStripSeparator())

        # Item 3: Slack 로그인 (Uses canonical InvestigationService login setup)
        item_login = WinForms.ToolStripMenuItem("Slack 로그인")

        def _on_login_click(sender, e):
            logger.info("Slack 로그인 triggered from System Tray.")
            self.open_main_window()
            if self._main_window and self._main_window.investigation_service:
                self._main_window.investigation_service.start_login_setup()

        item_login.Click += _on_login_click
        self._item_login = item_login
        menu.Items.Add(item_login)

        # Item 4: 설정
        item_settings = WinForms.ToolStripMenuItem("설정")

        def _on_settings_click(sender, e):
            logger.info("설정 triggered from System Tray.")
            self.open_main_window()
            if self._main_window and self._main_window.bridge:
                self._main_window.bridge.send_message(
                    UiMessage(type="settings.open", payload={})
                )

        item_settings.Click += _on_settings_click
        self._item_settings = item_settings
        menu.Items.Add(item_settings)

        # Separator
        menu.Items.Add(WinForms.ToolStripSeparator())

        # Item 5: Exit
        item_exit = WinForms.ToolStripMenuItem("종료")

        def _on_exit_click(sender, e):
            self.exit_app()

        item_exit.Click += _on_exit_click
        menu.Items.Add(item_exit)

        # Wire canonical status updates from InvestigationService
        if self._main_window and self._main_window.investigation_service:
            self._main_window.investigation_service.on_status_changed = self.update_tray_status

        # 3. Create NotifyIcon
        notify_icon = WinForms.NotifyIcon()
        notify_icon.Text = "PESU Agent"
        notify_icon.Icon = SystemIcons.Application
        notify_icon.ContextMenuStrip = menu
        notify_icon.Visible = True

        def _on_tray_click(sender, e):
            if e.Button == WinForms.MouseButtons.Left:
                self.open_main_window()

        notify_icon.MouseClick += _on_tray_click
        notify_icon.DoubleClick += _on_open_click
        self._notify_icon = notify_icon

        # 4. ApplicationContext
        self._context = WinForms.ApplicationContext()

    def update_tray_status(self, status: str, message: Optional[str] = None) -> None:
        """Updates Tray status menu item text consistently with canonical app status."""
        if not hasattr(self, "_item_status") or not self._item_status:
            return

        status_labels = {
            "ready": "Status: Ready",
            "investigating": "Status: Investigating",
            "slack_login_required": "Status: Slack Login Required",
            "slack_login_in_progress": "Status: Slack Login In Progress",
            "provider_setup_required": "Status: API Key Required",
            "error": "Status: Error",
        }
        label = status_labels.get(status.lower(), f"Status: {status.capitalize()}")

        def _update():
            try:
                self._item_status.Text = label
                if hasattr(self, "_item_login") and self._item_login:
                    self._item_login.Enabled = (status.lower() != "slack_login_in_progress")
            except Exception as ex:
                logger.warning(f"Failed to update tray status text: {ex}")

        if self._context_menu and hasattr(self._context_menu, "InvokeRequired") and self._context_menu.InvokeRequired:
            self._context_menu.BeginInvoke(_update)
        else:
            _update()

    def open_main_window(self) -> None:
        """Restores and activates the existing Main Window instance (reuses existing)."""
        if self._is_exiting:
            return
        if self._main_window:
            logger.info("Restoring existing Main Window from System Tray (Zero new instances).")
            self._main_window.show_and_activate()

    def exit_app(self) -> None:
        """Gracefully disposes Main Window and NotifyIcon, and terminates the application loop."""
        if self._is_exiting:
            return
        logger.info("Explicit exit requested from System Tray.")

        if (
            self._main_window
            and self._main_window.form
            and not self._main_window.is_disposed
            and getattr(self._main_window.form, "InvokeRequired", False) is True
        ):
            import clr
            clr.AddReference("System")
            from System import Action
            try:
                self._main_window.form.BeginInvoke(Action(self._exit_internal))
                return
            except Exception:
                pass

        self._exit_internal()

    def _exit_internal(self) -> None:
        self._is_exiting = True

        if self._main_window:
            self._main_window.dispose_window()
            self._main_window = None

        if self._notify_icon is not None:
            try:
                self._notify_icon.Visible = False
                self._notify_icon.Dispose()
            except Exception:
                pass
            self._notify_icon = None

        if self._context is not None:
            try:
                self._context.ExitThread()
            except Exception:
                pass

        import clr
        clr.AddReference("System.Windows.Forms")
        import System.Windows.Forms as WinForms
        try:
            WinForms.Application.Exit()
        except Exception:
            pass
        logger.info("PESU Product Shell terminated cleanly.")

    def start_sta_thread(
        self,
        show_initial_window: bool = False,
        ready_timeout: float = 10.0,
    ) -> Any:
        """Starts the Product Shell UI and message loop on a dedicated WinForms STA thread.

        Guarantees that WinForms components, WebView2, and the message loop are created
        and executed within a dedicated STA (Single-Threaded Apartment) thread.
        Blocks the calling thread until setup_ui() has completed and the message loop
        is running.

        Returns the .NET Thread instance.
        """
        import clr
        clr.AddReference("System.Threading")
        clr.AddReference("System.Windows.Forms")
        from System.Threading import Thread as DotNetThread, ThreadStart, ApartmentState
        import System.Windows.Forms as WinForms

        ready_event = threading.Event()
        self._exit_event.clear()
        error_box = [None]

        def _sta_worker():
            try:
                logger.info("Initializing PesuTrayApp on dedicated STA thread...")
                self.setup_ui()
                if show_initial_window and self._main_window:
                    self._main_window.show_and_activate()
                ready_event.set()
                logger.info("WinForms message loop starting on STA thread...")
                WinForms.Application.Run(self._context)
                logger.info("WinForms message loop ended.")
            except Exception as ex:
                logger.error(f"Error in Product Shell STA thread: {ex}", exc_info=True)
                error_box[0] = ex
                ready_event.set()
            finally:
                self._exit_event.set()

        sta_thread = DotNetThread(ThreadStart(_sta_worker))
        sta_thread.SetApartmentState(ApartmentState.STA)
        sta_thread.IsBackground = True
        self._sta_thread = sta_thread
        sta_thread.Start()

        if not ready_event.wait(timeout=ready_timeout):
            raise TimeoutError(f"Timed out waiting for Product Shell STA thread to initialize ({ready_timeout}s)")
        if error_box[0] is not None:
            raise RuntimeError(f"Product Shell STA thread initialization failed: {error_box[0]}") from error_box[0]

        return sta_thread

    def run(self, show_initial_window: bool = True) -> int:
        """Runs the Product Shell by launching canonical STA bootstrap and waiting for exit.

        Guarantees that the UI runs strictly on a dedicated STA thread regardless of
        whether the caller is STA, MTA, or an uninitialized thread.
        Blocks the calling thread until the application exits gracefully.
        """
        import clr
        clr.AddReference("System.Threading")
        from System.Threading import Thread as DotNetThread, ApartmentState

        # If calling thread is already STA, run message loop directly on it
        if DotNetThread.CurrentThread.GetApartmentState() == ApartmentState.STA:
            self.setup_ui()
            if show_initial_window and self._main_window:
                self._main_window.show_and_activate()
            import System.Windows.Forms as WinForms
            logger.info("🚀 [PRODUCT_SHELL] WinForms Application loop running on existing STA thread...")
            WinForms.Application.Run(self._context)
            return 0

        # Caller is MTA / non-STA (e.g. Python main thread in zero-argument startup):
        logger.info("🚀 [PRODUCT_SHELL] Launching Product Shell on dedicated STA thread...")
        self.start_sta_thread(show_initial_window=show_initial_window)

        try:
            while not self._exit_event.wait(timeout=0.2):
                pass
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received, shutting down PESU Product Shell...")
            self.exit_app()
            self._exit_event.wait(timeout=3.0)

        # Ensure STA thread has completely exited
        if self._sta_thread is not None:
            try:
                if hasattr(self._sta_thread, "Join"):
                    self._sta_thread.Join(3000)
            except Exception:
                pass

        return 0
