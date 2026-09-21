"""PESU Dedicated WinForms WebView2 Host Foundation Runtime (Phase 9.9.7).

Implements a standalone, zero-foreground, pre-CreateHandle hardened WebView2 host
for PESU Slack runtime without dependencies on pywebview internal window abstractions.

Architecture Invariants:
1. Pre-CreateHandle Hardening:
   - Form inherits from C# PesuNonActivatingForm with CreateParams overriding:
     * WS_EX_NOACTIVATE (0x08000000)
     * WS_EX_TOOLWINDOW (0x00000080)
   - ShowWithoutActivation returns true.
   - Native HWND has zero foreground acquisition at creation, initialization, show, navigation, and evaluation.
2. Rendered-Visible Offscreen Boundary:
   - Form positioned at (-32000, -32000) with Form.Visible=true and WebView2.Dock=Fill.
   - document.visibilityState resolves to "visible".
3. Minimal Adapter Contract:
   - evaluate_js(script: str) -> Any (JSON decoded to Python object)
   - destroy() -> None
   - native / hwnd properties for diagnostic telemetry
4. Isolated Dedicated Profile:
   - %LOCALAPPDATA%\\PESU\\slack-runtime-direct
   - Zero cookie/token/profile copying from Desktop Slack.
   - Unauthenticated state fails closed.
5. C# Form Assembly Strategy:
   - Precompiled build-time helper DLL (build/native/PesuHost.dll).
   - Zero runtime CodeDom compilation, zero compiler invocations.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from typing import Any, Optional
import urllib.parse

from pydantic import BaseModel, Field

from pesu_agent.parsers.slack_permalink_parser import parse_slack_message_permalink
from pesu_agent.runtime.resource_resolver import (
    PesuHostAssemblyError,
    PesuHostFailureCode,
    get_pesu_host_dll_path,
    setup_webview2_runtime_environment,
)

logger = logging.getLogger(__name__)

# Win32 Constants
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000

user32 = ctypes.windll.user32 if sys.platform == "win32" else None
kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None

if kernel32:
    kernel32.GetTickCount64.restype = ctypes.c_uint64
    kernel32.GetTickCount64.argtypes = []

_pesu_host_assembly = None
_assembly_lock = threading.Lock()

# Dedicated Production Direct Profile Directory
DIRECT_SLACK_PROFILE_PATH = str(
    Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "PESU" / "slack-runtime-direct"
)


def _load_pesu_host_assembly() -> Any:
    """Loads precompiled PesuHost.dll helper assembly into CLR.

    Runtime CodeDom compilation is strictly disabled.
    Fails closed if the DLL cannot be resolved or loaded.
    """
    global _pesu_host_assembly
    with _assembly_lock:
        if _pesu_host_assembly is not None:
            return _pesu_host_assembly

        import clr
        clr.AddReference("System")
        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")

        dll_path = get_pesu_host_dll_path()
        try:
            clr.AddReference(str(dll_path))
            import System.Reflection
            _pesu_host_assembly = System.Reflection.Assembly.LoadFrom(str(dll_path))
            return _pesu_host_assembly
        except Exception as err:
            raise PesuHostAssemblyError(
                PesuHostFailureCode.PESU_HOST_ASSEMBLY_LOAD_FAILED,
                f"Failed to load PesuHost.dll from {dll_path}: {err}",
            ) from err


def _load_webview2_dependencies() -> None:
    """Resolves and references Microsoft.Web.WebView2 assemblies and configures loader path."""
    setup_webview2_runtime_environment()



class VerifiedThreadNavigationRequest(BaseModel):
    """Strictly typed internal thread navigation request (Phase 12.3b/e).
    
    Invariants:
    - GENERIC_NAVIGATE_URL_API_EXPOSED = False
    - MODEL_SUPPLIED_URL_ALLOWED = False
    - WILDCARD_SLACK_ORIGIN_ALLOWED = False
    - ARCHIVE_PERMALINK_USED_FOR_NAVIGATION = False
    """

    channel_id: str = Field(description="Canonical Slack channel ID (e.g. 'C08U52XNGRX')")
    expected_root_ts: str = Field(description="Canonical thread root timestamp (seconds.microseconds)")
    target_route: Optional[str] = Field(default=None, description="Internal client SPA thread route (https://app.slack.com/client/...)")
    team_id: Optional[str] = Field(default=None, description="Canonical Slack team ID (e.g. 'T32QA15GC')")
    canonical_permalink: Optional[str] = Field(default=None, description="Canonical Slack permalink (archives URL) for reference")


class PesuWebViewHost:
    """Standalone PESU-Owned WebView2 Host conforming to the SearchBackend host contract."""

    def __init__(
        self,
        initial_url: str = "about:blank",
        user_data_folder: Optional[str] = None,
        x: int = -32000,
        y: int = -32000,
        width: int = 1280,
        height: int = 900,
        title: str = "PESU Dedicated Host Runtime",
    ) -> None:
        self.initial_url = initial_url
        self.user_data_folder = user_data_folder or DIRECT_SLACK_PROFILE_PATH
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.title = title

        self._thread: Optional[Any] = None
        self._form: Any = None
        self._webview: Any = None
        self._hwnd: Optional[int] = None
        self._initial_exstyle: Optional[int] = None

        self._ready_event = threading.Event()
        self._error: Optional[str] = None
        self._stopped_event = threading.Event()

    @property
    def hwnd(self) -> Optional[int]:
        """Returns the native Win32 window HWND."""
        return self._hwnd

    @property
    def native(self) -> Any:
        """Exposes the native Form instance for compatibility."""
        return self._form

    @property
    def initial_exstyle(self) -> Optional[int]:
        """Returns the ExStyle captured at first Handle creation."""
        return self._initial_exstyle

    @property
    def is_ready(self) -> bool:
        """Returns True if the host window and WebView2 control are ready."""
        return self._ready_event.is_set() and self._form is not None and self._webview is not None

    def start(self, timeout: float = 20.0) -> None:
        """Starts the STA thread and waits for CoreWebView2 initialization & navigation."""
        if self._thread is not None:
            is_alive = self._thread.IsAlive if hasattr(self._thread, "IsAlive") else self._thread.is_alive()
            if is_alive:
                return

        self._ready_event.clear()
        self._error = None

        def _sta_worker():
            try:
                _load_pesu_host_assembly()
                _load_webview2_dependencies()

                import clr
                import System
                from System.Drawing import Point, Size
                import System.Windows.Forms as WinForms
                from Microsoft.Web.WebView2.WinForms import (
                    CoreWebView2CreationProperties,
                    WebView2,
                )
                from PesuAgent.Host import PesuNonActivatingForm

                # 1. Instantiate PesuNonActivatingForm
                form = PesuNonActivatingForm()
                self._form = form
                form.Text = self.title
                form.StartPosition = WinForms.FormStartPosition.Manual
                form.Location = Point(self.x, self.y)
                form.Size = Size(self.width, self.height)

                # 2. Access Handle to create native HWND and capture initial ExStyle
                hwnd_val = form.Handle.ToInt32()
                self._hwnd = hwnd_val
                if user32:
                    self._initial_exstyle = user32.GetWindowLongW(hwnd_val, GWL_EXSTYLE)

                # 3. Create and attach WebView2 control
                webview = WebView2()
                self._webview = webview
                props = CoreWebView2CreationProperties()
                props.UserDataFolder = self.user_data_folder
                props.AdditionalBrowserArguments = "--disable-features=ElasticOverscroll"
                webview.CreationProperties = props
                form.Controls.Add(webview)
                webview.Dock = WinForms.DockStyle.Fill
                webview.BringToFront()

                def on_webview_ready(sender, args):
                    try:
                        if args.IsSuccess:
                            if self.initial_url and self.initial_url != "about:blank":
                                webview.CoreWebView2.Navigate(self.initial_url)
                            else:
                                self._ready_event.set()
                        else:
                            self._error = f"CoreWebView2 initialization failed: {args.InitializationException}"
                            self._ready_event.set()
                    except Exception as ex:
                        self._error = str(ex)
                        self._ready_event.set()

                def on_navigation_completed(sender, args):
                    self._ready_event.set()

                webview.CoreWebView2InitializationCompleted += on_webview_ready
                webview.NavigationCompleted += on_navigation_completed

                # 4. Initialize CoreWebView2
                webview.EnsureCoreWebView2Async(None)

                # 5. Show Form (ShowWithoutActivation = true)
                form.Show()

                # 6. Run STA message loop
                WinForms.Application.Run(form)
            except Exception as e:
                self._error = str(e)
                self._ready_event.set()
            finally:
                self._stopped_event.set()

        import clr
        clr.AddReference("System.Threading")
        from System.Threading import Thread as DotNetThread, ThreadStart, ApartmentState

        self._thread = DotNetThread(ThreadStart(_sta_worker))
        self._thread.SetApartmentState(ApartmentState.STA)
        self._thread.Start()

        if not self._ready_event.wait(timeout):
            raise TimeoutError(f"PesuWebViewHost failed to initialize within {timeout}s: error={self._error}")

        if self._error:
            raise RuntimeError(f"PesuWebViewHost startup error: {self._error}")

    def evaluate_js(self, script: str, timeout: float = 10.0) -> Any:
        """Executes JavaScript synchronously and returns parsed JSON Python value."""
        if not self._webview or not self._ready_event.is_set():
            raise RuntimeError("PesuWebViewHost is not ready for JavaScript evaluation.")

        import clr
        from System import Action, Func, Object, String
        from System.Threading.Tasks import Task

        result_container: list[Any] = [None]
        error_container: list[Optional[Exception]] = [None]
        done_event = threading.Event()

        def _execute_on_sta():
            try:
                task = self._webview.ExecuteScriptAsync(script)

                def _on_task_completed(completed_task):
                    try:
                        if completed_task.IsFaulted:
                            error_container[0] = RuntimeError(f"ExecuteScriptAsync faulted: {completed_task.Exception}")
                        elif completed_task.IsCanceled:
                            error_container[0] = RuntimeError("ExecuteScriptAsync was canceled.")
                        else:
                            result_container[0] = completed_task.Result
                    except Exception as ex:
                        error_container[0] = ex
                    finally:
                        done_event.set()

                task.ContinueWith(Action[Task[String]](_on_task_completed))
            except Exception as ex:
                error_container[0] = ex
                done_event.set()

        if self._form.InvokeRequired:
            self._form.Invoke(Action(_execute_on_sta))
        else:
            _execute_on_sta()

        if not done_event.wait(timeout):
            raise TimeoutError(f"evaluate_js timed out after {timeout}s")

        if error_container[0]:
            raise error_container[0]

        raw_result = result_container[0]
        if raw_result is None or raw_result == "" or raw_result == "null":
            return None

        try:
            return json.loads(raw_result)
        except Exception:
            return raw_result

    def get_runtime_qualified_origins(self) -> set[str]:
        """Inspects runtime host location to discover strictly qualified Slack origins.

        Security Invariants:
        - WILDCARD_SLACK_ORIGIN_ALLOWED = False (*.slack.com wildcard disallowed)
        - NO_DOMAIN_SPECIFIC_HARDCODING = True (zero hardcoded tenant/company domains)
        """
        origins: set[str] = {"https://app.slack.com"}
        if not self.is_ready:
            return origins

        try:
            js_query = r"""
            (() => {
                const found = [];
                if (window.location && window.location.origin && window.location.origin.startsWith('https://')) {
                    found.push(window.location.origin);
                }
                const tsBoot = (window.TS && window.TS.boot_data) ? window.TS.boot_data : window.boot_data;
                if (tsBoot) {
                    if (tsBoot.team_url) {
                        try {
                            const u = new URL(tsBoot.team_url);
                            if (u.protocol === 'https:' && u.hostname.endsWith('.slack.com')) {
                                found.push(u.origin);
                            }
                        } catch(e) {}
                    }
                    if (tsBoot.team_domain && typeof tsBoot.team_domain === 'string') {
                        found.push(`https://${tsBoot.team_domain}.slack.com`);
                    }
                }
                const anchors = Array.from(document.querySelectorAll('a[href*=".slack.com/archives/"]'));
                for (const a of anchors) {
                    try {
                        const u = new URL(a.href);
                        if (u.protocol === 'https:' && u.hostname.endsWith('.slack.com')) {
                            found.push(u.origin);
                        }
                    } catch(e) {}
                }
                return found;
            })()
            """
            res = self.evaluate_js(js_query, timeout=3.0)
            if isinstance(res, list):
                for o in res:
                    if isinstance(o, str) and o.startswith("https://") and o.endswith(".slack.com"):
                        origins.add(o)
        except Exception as e:
            logger.debug(f"Could not inspect runtime origins: {e}")
        return origins

    def get_active_team_id(self) -> Optional[str]:
        """Inspects active authenticated Slack team ID dynamically without hardcoding."""
        if not self.is_ready:
            return None
        try:
            js_query = r"""
            (() => {
                if (window.location && window.location.pathname) {
                    const m = window.location.pathname.match(/\/client\/([A-Z0-9]+)/);
                    if (m) return m[1];
                }
                const tsBoot = (window.TS && window.TS.boot_data) ? window.TS.boot_data : window.boot_data;
                if (tsBoot && tsBoot.team_id) {
                    return tsBoot.team_id;
                }
                return null;
            })()
            """
            res = self.evaluate_js(js_query, timeout=3.0)
            if isinstance(res, str) and re.match(r"^[A-Z0-9]+$", res):
                return res
        except Exception as e:
            logger.debug(f"Could not inspect active team ID: {e}")
        return None

    def navigate_thread_context(
        self,
        request: VerifiedThreadNavigationRequest,
        timeout: float = 20.0,
    ) -> bool:
        """Navigates offscreen host to a validated internal Slack Web client SPA thread route.

        Security Invariants:
        - GENERIC_NAVIGATE_URL_API_EXPOSED = False (No generic navigate(url: str) method)
        - MODEL_SUPPLIED_URL_ALLOWED = False (Only typed VerifiedThreadNavigationRequest)
        - WILDCARD_SLACK_ORIGIN_ALLOWED = False (Strict https://app.slack.com origin only)
        - ARCHIVE_PERMALINK_USED_FOR_NAVIGATION = False (Archive permalinks strictly rejected)
        - UNDOCUMENTED_THREAD_ROUTE_INVENTED = False (Internal client SPA thread route only)
        """
        if not isinstance(request, VerifiedThreadNavigationRequest):
            raise TypeError("request must be an instance of VerifiedThreadNavigationRequest")

        if not self.is_ready:
            raise RuntimeError("PesuWebViewHost is not ready for thread navigation.")

        target = request.target_route or request.canonical_permalink
        if not target:
            logger.error("Thread navigation rejected: missing target route.")
            return False

        # 1. Enforce ARCHIVE_PERMALINK_USED_FOR_NAVIGATION = False
        if "/archives/" in target:
            logger.error(
                f"Thread navigation rejected: archive permalink '{target}' must never be used as a navigation target. "
                "Must use internal client SPA route to prevent unloading Slack SPA."
            )
            return False

        # 2. Strict Internal Client SPA Route Regex Validation
        INTERNAL_THREAD_RE = re.compile(
            r"^https://app\.slack\.com/client/(?P<team_id>[A-Z0-9]+)/(?P<channel_id>[A-Z0-9]+)/thread/(?P<thread_channel>[A-Z0-9]+)-(?P<root_ts>\d+\.\d+)$"
        )
        m = INTERNAL_THREAD_RE.match(target)
        if not m:
            logger.error(f"Thread navigation rejected: target '{target}' is not a valid internal client SPA thread route.")
            return False

        route_team = m.group("team_id")
        route_channel = m.group("channel_id")
        thread_channel = m.group("thread_channel")
        route_root_ts = m.group("root_ts")

        if route_channel != thread_channel or route_channel != request.channel_id:
            logger.error(
                f"Thread navigation rejected: channel mismatch ({route_channel} vs {request.channel_id})."
            )
            return False

        active_team = self.get_active_team_id()
        if active_team and active_team != route_team:
            logger.error(
                f"Thread navigation rejected: team mismatch ({route_team} != active {active_team})."
            )
            return False

        # Timestamp normalization and validation
        def _norm_ts(ts: Optional[str]) -> Optional[str]:
            if not ts or not isinstance(ts, str):
                return None
            m_ts = re.match(r"^(\d+)(?:\.(\d+))?$", ts.strip())
            if not m_ts:
                return None
            return f"{m_ts.group(1)}.{(m_ts.group(2) or '').ljust(6, '0')[:6]}"

        norm_expected_root = _norm_ts(request.expected_root_ts)
        norm_route_root = _norm_ts(route_root_ts)
        if norm_expected_root != norm_route_root:
            logger.error(
                f"Thread navigation rejected: timestamp mismatch ({norm_route_root} != {norm_expected_root})."
            )
            return False

        # 3. Origin validation: exact match against https://app.slack.com
        parsed_url = urllib.parse.urlsplit(target)
        target_origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        if target_origin != "https://app.slack.com":
            logger.error(
                f"Thread navigation rejected: origin '{target_origin}' != 'https://app.slack.com'."
            )
            return False

        # 4. Execute navigation on STA thread
        import clr
        from System import Action

        nav_done_event = threading.Event()
        nav_error: list[Optional[str]] = [None]

        def _navigate_on_sta():
            try:
                def _on_nav_completed(sender, args):
                    try:
                        if not args.IsSuccess:
                            nav_error[0] = str(args.WebErrorStatus)
                    finally:
                        try:
                            self._webview.NavigationCompleted -= _on_nav_completed
                        except Exception:
                            pass
                        nav_done_event.set()

                self._webview.NavigationCompleted += _on_nav_completed
                self._webview.CoreWebView2.Navigate(target)
            except Exception as ex:
                nav_error[0] = str(ex)
                nav_done_event.set()

        if self._form.InvokeRequired:
            self._form.Invoke(Action(_navigate_on_sta))
        else:
            _navigate_on_sta()

        if not nav_done_event.wait(timeout):
            logger.warning(f"Thread navigation timed out after {timeout}s.")
            return False

        if nav_error[0]:
            logger.warning(f"Thread navigation completed with error: {nav_error[0]}")
            return False

        return True

    def destroy(self) -> None:
        """Gracefully closes the Form and terminates the STA event loop."""
        if self._form:
            import clr
            from System import Action

            def _close_on_sta():
                try:
                    if self._webview:
                        self._webview.Dispose()
                        self._webview = None
                    if self._form:
                        self._form.Close()
                        self._form.Dispose()
                        self._form = None
                except Exception:
                    pass

            try:
                if self._form and hasattr(self._form, "InvokeRequired") and self._form.InvokeRequired:
                    self._form.Invoke(Action(_close_on_sta))
                elif self._form:
                    _close_on_sta()
            except Exception:
                pass

        if self._thread:
            try:
                is_alive = self._thread.IsAlive if hasattr(self._thread, "IsAlive") else self._thread.is_alive()
                if is_alive:
                    if hasattr(self._thread, "Join"):
                        self._thread.Join(2000)
                    else:
                        self._thread.join(timeout=2.0)
            except Exception:
                pass
        self._thread = None
        self._hwnd = None
        self._ready_event.clear()


class PesuInteractiveLoginHost:
    """Interactive Onscreen Host dedicated solely to 1-time human login setup (--login-setup).

    Completely isolated from background non-activating runtime.
    Zero automation, zero cookie copying, zero token extraction, zero search execution.
    """

    def __init__(
        self,
        user_data_folder: Optional[str] = None,
        initial_url: str = "https://app.slack.com/client",
        title: str = "PESU Slack Direct Profile Setup (Log In and Close Window When Finished)",
    ) -> None:
        self.user_data_folder = user_data_folder or DIRECT_SLACK_PROFILE_PATH
        self.initial_url = initial_url
        self.title = title

        self._thread: Optional[Any] = None
        self._form: Any = None
        self._webview: Any = None
        self._ready_event = threading.Event()
        self._closed_event = threading.Event()
        self._error: Optional[str] = None

    def start_and_wait(self) -> None:
        """Starts the interactive onscreen GUI and blocks until the human closes the window."""
        _load_webview2_dependencies()

        import clr
        import System
        from System.Drawing import Point, Size
        import System.Windows.Forms as WinForms
        from Microsoft.Web.WebView2.WinForms import (
            CoreWebView2CreationProperties,
            WebView2,
        )

        def _sta_worker():
            try:
                # 1. Standard activating onscreen WinForms Form
                form = WinForms.Form()
                self._form = form
                form.Text = self.title
                form.StartPosition = WinForms.FormStartPosition.CenterScreen
                form.Size = Size(1280, 900)

                # 2. Attach WebView2 control
                webview = WebView2()
                self._webview = webview
                props = CoreWebView2CreationProperties()
                props.UserDataFolder = self.user_data_folder
                webview.CreationProperties = props
                form.Controls.Add(webview)
                webview.Dock = WinForms.DockStyle.Fill
                webview.BringToFront()

                def on_webview_ready(sender, args):
                    try:
                        if args.IsSuccess:
                            webview.CoreWebView2.Navigate(self.initial_url)
                            self._ready_event.set()
                        else:
                            self._error = f"CoreWebView2 init failed: {args.InitializationException}"
                            self._ready_event.set()
                    except Exception as ex:
                        self._error = str(ex)
                        self._ready_event.set()

                def on_form_closed(sender, args):
                    self._closed_event.set()

                webview.CoreWebView2InitializationCompleted += on_webview_ready
                form.FormClosed += on_form_closed

                webview.EnsureCoreWebView2Async(None)
                form.Show()
                WinForms.Application.Run(form)
            except Exception as e:
                self._error = str(e)
                self._ready_event.set()
                self._closed_event.set()

        clr.AddReference("System.Threading")
        from System.Threading import Thread as DotNetThread, ThreadStart, ApartmentState

        self._thread = DotNetThread(ThreadStart(_sta_worker))
        self._thread.SetApartmentState(ApartmentState.STA)
        self._thread.Start()

        self._closed_event.wait()

    def destroy(self) -> None:
        """Closes the interactive host."""
        if self._form:
            import clr
            from System import Action

            def _close():
                try:
                    if self._form:
                        self._form.Close()
                except Exception:
                    pass

            try:
                if self._form.InvokeRequired:
                    self._form.Invoke(Action(_close))
                else:
                    _close()
            except Exception:
                pass
        self._closed_event.set()
