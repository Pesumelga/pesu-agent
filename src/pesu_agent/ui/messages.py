"""Typed Message Protocol for PESU Product UI & WebView2 Bridge (Phase 10.2).

Guarantees:
1. Strict Typed Envelope:
   - version: int (must be 1)
   - type: str (must be in allowlist)
   - request_id: Optional[str]
   - payload: dict[str, Any] (bounded <= 64KB)
2. Closed Allowlists:
   - JS -> Python: ui.ready, ui.ping, query.draft_changed, query.submit
   - Python -> JS: app.ready, app.status, bridge.pong, query.submit_ack, app.error
3. Fail-Closed Validation:
   - Malformed JSON, unknown types, oversized payloads, unsupported versions are rejected.
   - Arbitrary string execution and arbitrary RPCs are strictly prohibited.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROTOCOL_VERSION: int = 1
MAX_MESSAGE_PAYLOAD_BYTES: int = 65536  # 64 KB

JS_TO_PYTHON_ALLOWED_TYPES: frozenset[str] = frozenset({
    "ui.ready",
    "ui.ping",
    "query.draft_changed",
    "query.submit",
    "citation.open",
    "consent.external_analysis.grant",
    "consent.external_analysis.cancel",
    "slack.login.request",
    "provider.openrouter.key.save",
    "provider.openrouter.key.remove",
    "provider.openrouter.key.cancel",
    "provider.openrouter.status.get",
    "settings.open",
    "settings.close",
    "app.status.request",
    "onboarding.complete",
    "onboarding.state.get",
    "startup.auto.status.request",
    "startup.auto.enable",
    "startup.auto.disable",
    "history.list",
    "history.open",
    "history.delete",
    "history.clear",
    "history.status.get",
    "history.exit",
})

PYTHON_TO_JS_ALLOWED_TYPES: frozenset[str] = frozenset({
    "app.ready",
    "app.status",
    "bridge.pong",
    "query.submit_ack",
    "app.error",
    "investigation.accepted",
    "investigation.progress",
    "investigation.completed",
    "investigation.failed",
    "investigation.busy",
    "citation.open_ack",
    "consent.external_analysis.required",
    "consent.external_analysis.state",
    "slack.login.started",
    "slack.login.completed",
    "slack.login.failed",
    "slack.login.busy",
    "provider.openrouter.status",
    "provider.openrouter.setup.required",
    "provider.openrouter.key.saved",
    "provider.openrouter.key.removed",
    "provider.openrouter.error",
    "settings.open",
    "settings.close",
    "onboarding.show",
    "onboarding.dismiss",
    "startup.auto.status",
    "startup.auto.error",
    "history.list.result",
    "history.record",
    "history.deleted",
    "history.cleared",
    "history.status",
    "history.error",
})

MESSAGE_TYPE_ALLOWLIST: bool = True
ARBITRARY_JS_EXECUTION_BRIDGE: bool = False
ARBITRARY_PYTHON_METHOD_BRIDGE: bool = False


class MessageValidationError(ValueError):
    """Raised when an incoming or outgoing envelope fails strict contract validation."""


@dataclass
class UiMessage:
    """Standard typed envelope for all bridge communications."""

    type: str
    version: int = PROTOCOL_VERSION
    request_id: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.type,
            "request_id": self.request_id,
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def parse_and_validate_incoming_message(raw_json: str) -> UiMessage:
    """Parses and validates a message sent from JavaScript (JS -> Python).

    Fail-closed rules:
    - Must be valid JSON string under 64KB
    - Must decode to a dictionary
    - version must equal PROTOCOL_VERSION (1)
    - type must be in JS_TO_PYTHON_ALLOWED_TYPES
    - payload must be a dictionary
    """
    if not isinstance(raw_json, str):
        raise MessageValidationError(f"Expected str, got {type(raw_json).__name__}")

    if len(raw_json.encode("utf-8")) > MAX_MESSAGE_PAYLOAD_BYTES:
        raise MessageValidationError(
            f"Message size {len(raw_json.encode('utf-8'))} bytes exceeds limit {MAX_MESSAGE_PAYLOAD_BYTES}"
        )

    try:
        data = json.loads(raw_json)
    except Exception as e:
        raise MessageValidationError(f"Malformed JSON: {e}") from e

    if not isinstance(data, dict):
        raise MessageValidationError(f"Root JSON must be object, got {type(data).__name__}")

    version = data.get("version")
    if version != PROTOCOL_VERSION:
        raise MessageValidationError(
            f"Unsupported protocol version {version!r}; expected {PROTOCOL_VERSION}"
        )

    msg_type = data.get("type")
    if not isinstance(msg_type, str):
        raise MessageValidationError("Missing or non-string 'type' field")

    if msg_type not in JS_TO_PYTHON_ALLOWED_TYPES:
        raise MessageValidationError(f"Message type {msg_type!r} is not in JS->Python allowlist")

    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise MessageValidationError(f"'payload' must be a dictionary, got {type(payload).__name__}")

    request_id = data.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        raise MessageValidationError(f"'request_id' must be a string or null, got {type(request_id).__name__}")

    return UiMessage(
        version=version,
        type=msg_type,
        request_id=request_id,
        payload=payload,
    )


def serialize_outgoing_message(msg: UiMessage) -> str:
    """Serializes a message to be sent to JavaScript (Python -> JS).

    Fail-closed rules:
    - version must equal PROTOCOL_VERSION (1)
    - type must be in PYTHON_TO_JS_ALLOWED_TYPES
    - payload must be a dictionary
    """
    if msg.version != PROTOCOL_VERSION:
        raise MessageValidationError(
            f"Unsupported protocol version {msg.version!r}; expected {PROTOCOL_VERSION}"
        )

    if msg.type not in PYTHON_TO_JS_ALLOWED_TYPES:
        raise MessageValidationError(f"Message type {msg.type!r} is not in Python->JS allowlist")

    if not isinstance(msg.payload, dict):
        raise MessageValidationError(f"'payload' must be a dictionary, got {type(msg.payload).__name__}")

    raw = msg.to_json()
    if len(raw.encode("utf-8")) > MAX_MESSAGE_PAYLOAD_BYTES:
        raise MessageValidationError(
            f"Outgoing message size exceeds maximum allowed {MAX_MESSAGE_PAYLOAD_BYTES} bytes"
        )

    return raw
