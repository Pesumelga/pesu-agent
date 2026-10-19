"""Native WebView2 Typed Message Bridge (Phase 10.2).

Bridges communication between JavaScript in Product UI and Python backend via:
- JS -> Python: window.chrome.webview.postMessage -> CoreWebView2.WebMessageReceived
- Python -> JS: CoreWebView2.PostWebMessageAsJson -> window.chrome.webview.addEventListener('message')

Guarantees:
1. Fail-closed message parsing and allowlist validation.
2. Initial handshake: UI must send 'ui.ready' before Python pushes 'app.ready' and 'app.status'.
3. Thread-safe message dispatch using Form.BeginInvoke if cross-thread.
4. Arbitrary JS evaluation or RPC execution is completely prohibited.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from pesu_agent.ui.messages import (
    MessageValidationError,
    UiMessage,
    parse_and_validate_incoming_message,
    serialize_outgoing_message,
)

logger = logging.getLogger(__name__)


class ProductWebViewBridge:
    """Manages typed bidirectional message exchange with the Product UI WebView2."""

    def __init__(
        self,
        webview: Any = None,
        form: Any = None,
        on_query_submit: Optional[Callable[[str, Optional[str]], None]] = None,
        on_citation_open: Optional[Callable[[str, Optional[str]], None]] = None,
        on_consent_grant: Optional[Callable[[Optional[str]], None]] = None,
        on_consent_cancel: Optional[Callable[[Optional[str]], None]] = None,
        on_login_request: Optional[Callable[[Optional[str]], None]] = None,
        on_openrouter_key_save: Optional[Callable[[str, Optional[str]], None]] = None,
        on_openrouter_key_remove: Optional[Callable[[Optional[str]], None]] = None,
        on_openrouter_key_cancel: Optional[Callable[[Optional[str]], None]] = None,
        on_openrouter_status_get: Optional[Callable[[], None]] = None,
        on_status_request: Optional[Callable[[], None]] = None,
        on_onboarding_complete: Optional[Callable[[], None]] = None,
        on_onboarding_state_get: Optional[Callable[[], None]] = None,
        on_autostart_status_get: Optional[Callable[[Optional[str]], None]] = None,
        on_autostart_enable: Optional[Callable[[Optional[str]], None]] = None,
        on_autostart_disable: Optional[Callable[[Optional[str]], None]] = None,
        on_history_list: Optional[Callable[[int, int, Optional[str]], None]] = None,
        on_history_open: Optional[Callable[[str, Optional[str]], None]] = None,
        on_history_delete: Optional[Callable[[str, Optional[str]], None]] = None,
        on_history_clear: Optional[Callable[[Optional[str]], None]] = None,
        on_history_status_get: Optional[Callable[[Optional[str]], None]] = None,
        on_history_exit: Optional[Callable[[], None]] = None,
    ) -> None:
        self._webview = webview
        self._form = form
        self._on_query_submit = on_query_submit
        self._on_citation_open = on_citation_open
        self._on_consent_grant = on_consent_grant
        self._on_consent_cancel = on_consent_cancel
        self._on_login_request = on_login_request
        self._on_openrouter_key_save = on_openrouter_key_save
        self._on_openrouter_key_remove = on_openrouter_key_remove
        self._on_openrouter_key_cancel = on_openrouter_key_cancel
        self._on_openrouter_status_get = on_openrouter_status_get
        self._on_status_request = on_status_request
        self._on_onboarding_complete = on_onboarding_complete
        self._on_onboarding_state_get = on_onboarding_state_get
        self._on_autostart_status_get = on_autostart_status_get
        self._on_autostart_enable = on_autostart_enable
        self._on_autostart_disable = on_autostart_disable
        self._on_history_list = on_history_list
        self._on_history_open = on_history_open
        self._on_history_delete = on_history_delete
        self._on_history_clear = on_history_clear
        self._on_history_status_get = on_history_status_get
        self._on_history_exit = on_history_exit

        self.ui_ready: bool = False
        self.messages_received_count: int = 0
        self.messages_sent_count: int = 0
        self.last_received_message: Optional[UiMessage] = None
        self.last_sent_message: Optional[UiMessage] = None
        self.last_draft_query: str = ""

    def attach_to_webview(self, webview: Any, form: Any = None) -> None:
        """Wires up WebMessageReceived listener on the CoreWebView2 instance."""
        self._webview = webview
        if form is not None:
            self._form = form

        def _on_web_message_received(sender, args):
            try:
                # args.WebMessageAsJson returns the JSON string sent via postMessage
                raw_json = getattr(args, "WebMessageAsJson", None)
                if raw_json is None and hasattr(args, "TryGetWebMessageAsString"):
                    raw_json = args.TryGetWebMessageAsString()
                if raw_json:
                    self.handle_incoming_raw(raw_json)
            except Exception as e:
                logger.error(f"Error handling WebMessageReceived: {e}", exc_info=True)

        webview.CoreWebView2.WebMessageReceived += _on_web_message_received
        logger.info("ProductWebViewBridge attached to CoreWebView2.")

    def handle_incoming_raw(self, raw_json: str) -> Optional[UiMessage]:
        """Parses, validates, and dispatches an incoming message from JS."""
        try:
            msg = parse_and_validate_incoming_message(raw_json)
        except MessageValidationError as err:
            logger.warning(f"Rejected invalid UI message: {err} | Raw: {raw_json[:120]}")
            # Try to send back app.error if request_id can be extracted cheaply
            try:
                import json
                data = json.loads(raw_json)
                req_id = data.get("request_id") if isinstance(data, dict) else None
                if req_id and isinstance(req_id, str):
                    self.send_message(
                        UiMessage(
                            type="app.error",
                            request_id=req_id,
                            payload={"error": str(err)},
                        )
                    )
            except Exception:
                pass
            return None

        self.messages_received_count += 1
        self.last_received_message = msg
        self._dispatch_message(msg)
        return msg

    def _dispatch_message(self, msg: UiMessage) -> None:
        """Dispatches validated incoming message to appropriate handler."""
        if msg.type == "ui.ready":
            self.ui_ready = True
            logger.info("Handshake received: ui.ready. Pushing app.ready & app.status.")
            self.send_message(
                UiMessage(
                    type="app.ready",
                    request_id=msg.request_id,
                    payload={"ready": True, "read_only": True},
                )
            )
            self.send_message(
                UiMessage(
                    type="app.status",
                    payload={"status": "ready", "message": "준비됨"},
                )
            )
            if self._on_openrouter_status_get is not None:
                self._on_openrouter_status_get()

        elif msg.type == "ui.ping":
            logger.debug(f"Ping received (req_id={msg.request_id}). Responding with bridge.pong.")
            self.send_message(
                UiMessage(
                    type="bridge.pong",
                    request_id=msg.request_id,
                    payload={"timestamp": time.time()},
                )
            )

        elif msg.type == "query.draft_changed":
            draft = msg.payload.get("query", "")
            if isinstance(draft, str):
                self.last_draft_query = draft

        elif msg.type == "query.submit":
            query_text = msg.payload.get("query", "")
            logger.info(f"Received query.submit: '{query_text[:50]}' (req_id={msg.request_id})")

            if self._on_query_submit is not None:
                self._on_query_submit(query_text, msg.request_id)
            else:
                # Diagnostic ack fallback if no worker attached
                self.send_message(
                    UiMessage(
                        type="query.submit_ack",
                        request_id=msg.request_id,
                        payload={
                            "accepted": True,
                            "diagnostic": True,
                            "message": "연결 확인 완료 (진단 모드)",
                        },
                    )
                )
                self.send_message(
                    UiMessage(
                        type="app.status",
                        payload={"status": "ready", "message": "준비됨 (진단 요청 확인 완료)"},
                    )
                )

        elif msg.type == "citation.open":
            evidence_id = msg.payload.get("evidence_id")
            logger.info(f"Received citation.open: evidence_id='{evidence_id}' (req_id={msg.request_id})")
            if self._on_citation_open is not None and evidence_id:
                self._on_citation_open(str(evidence_id), msg.request_id)
            else:
                self.send_message(
                    UiMessage(
                        type="citation.open_ack",
                        request_id=msg.request_id,
                        payload={
                            "evidence_id": evidence_id,
                            "success": False,
                            "reason": "NO_HANDLER_OR_EMPTY_ID",
                        },
                    )
                )

        elif msg.type == "consent.external_analysis.grant":
            logger.info(f"Received consent.external_analysis.grant (req_id={msg.request_id})")
            if self._on_consent_grant is not None:
                self._on_consent_grant(msg.request_id)

        elif msg.type == "consent.external_analysis.cancel":
            logger.info(f"Received consent.external_analysis.cancel (req_id={msg.request_id})")
            if self._on_consent_cancel is not None:
                self._on_consent_cancel(msg.request_id)

        elif msg.type == "slack.login.request":
            logger.info(f"Received slack.login.request (req_id={msg.request_id})")
            if self._on_login_request is not None:
                self._on_login_request(msg.request_id)

        elif msg.type == "provider.openrouter.key.save":
            key_val = msg.payload.get("key", "")
            logger.info(f"Received provider.openrouter.key.save (req_id={msg.request_id})")
            if self._on_openrouter_key_save is not None:
                self._on_openrouter_key_save(str(key_val), msg.request_id)

        elif msg.type == "provider.openrouter.key.remove":
            logger.info(f"Received provider.openrouter.key.remove (req_id={msg.request_id})")
            if self._on_openrouter_key_remove is not None:
                self._on_openrouter_key_remove(msg.request_id)

        elif msg.type == "provider.openrouter.key.cancel":
            logger.info(f"Received provider.openrouter.key.cancel (req_id={msg.request_id})")
            if self._on_openrouter_key_cancel is not None:
                self._on_openrouter_key_cancel(msg.request_id)

        elif msg.type == "provider.openrouter.status.get":
            if self._on_openrouter_status_get is not None:
                self._on_openrouter_status_get()

        elif msg.type == "app.status.request":
            if self._on_status_request is not None:
                self._on_status_request()
            elif self._on_openrouter_status_get is not None:
                self._on_openrouter_status_get()

        elif msg.type == "onboarding.complete":
            if self._on_onboarding_complete is not None:
                self._on_onboarding_complete()

        elif msg.type == "onboarding.state.get":
            if self._on_onboarding_state_get is not None:
                self._on_onboarding_state_get()

        elif msg.type == "startup.auto.status.request":
            if self._on_autostart_status_get is not None:
                self._on_autostart_status_get(msg.request_id)

        elif msg.type == "startup.auto.enable":
            if self._on_autostart_enable is not None:
                self._on_autostart_enable(msg.request_id)

        elif msg.type == "startup.auto.disable":
            if self._on_autostart_disable is not None:
                self._on_autostart_disable(msg.request_id)

        elif msg.type == "history.list":
            limit = msg.payload.get("limit", 20)
            offset = msg.payload.get("offset", 0)
            try:
                limit = int(limit)
            except (ValueError, TypeError):
                limit = 20
            try:
                offset = int(offset)
            except (ValueError, TypeError):
                offset = 0
            if self._on_history_list is not None:
                self._on_history_list(limit, offset, msg.request_id)

        elif msg.type == "history.open":
            history_id = msg.payload.get("id", "")
            if self._on_history_open is not None and history_id:
                self._on_history_open(str(history_id), msg.request_id)

        elif msg.type == "history.delete":
            history_id = msg.payload.get("id", "")
            if self._on_history_delete is not None and history_id:
                self._on_history_delete(str(history_id), msg.request_id)

        elif msg.type == "history.clear":
            if self._on_history_clear is not None:
                self._on_history_clear(msg.request_id)

        elif msg.type == "history.status.get":
            if self._on_history_status_get is not None:
                self._on_history_status_get(msg.request_id)

        elif msg.type == "history.exit":
            if self._on_history_exit is not None:
                self._on_history_exit()

    def send_message(self, msg: UiMessage) -> None:
        """Sends a typed message to JavaScript via CoreWebView2.PostWebMessageAsJson."""
        raw_json = serialize_outgoing_message(msg)

        def _post():
            if self._webview is None:
                logger.warning(f"Cannot send message {msg.type}: WebView2 control is None")
                return
            try:
                core = getattr(self._webview, "CoreWebView2", None)
                if core is None:
                    logger.warning(f"Cannot send message {msg.type}: CoreWebView2 is not initialized")
                    return
                core.PostWebMessageAsJson(raw_json)
                self.messages_sent_count += 1
                self.last_sent_message = msg
                logger.debug(f"Posted to JS: {msg.type}")
            except Exception as ex:
                logger.error(f"Failed to post WebMessage {msg.type}: {ex}")

        if (
            self._form is not None
            and getattr(self._form, "InvokeRequired", False) is True
        ):
            import clr
            clr.AddReference("System")
            from System import Action
            self._form.BeginInvoke(Action(_post))
        else:
            _post()
