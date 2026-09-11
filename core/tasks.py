import asyncio
import inspect
import json
import logging
import os
import re
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from utils.logger import setup_logger
from utils.config import get_config, get_userData
from core.msg_builder import build_message, build_message_with_openai
from core.browser import get_browser


complates = {}

logger = setup_logger(level=logging.DEBUG)


_INSTALL_MESSAGE_CONFIRMATION_OBSERVER_SCRIPT = r"""
({ token, message }) => {
  const normalize = (value) => String(value || "")
    .replace(/[\u200B-\u200D\u2060\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const wanted = normalize(message);
  if (!wanted) return { installed: false, reason: "empty_message" };

  const root = document.querySelector("#sub-app") || document.body;
  if (!root) return { installed: false, reason: "missing_root" };
  const editorSelector = [
    '[contenteditable="true"]',
    "textarea",
    'input:not([type="hidden"])',
    '[class*="chat-input-"]'
  ].join(",");
  const registryName = "__sparkMessageConfirmationV2";
  const registry = window[registryName] || (window[registryName] = new Map());
  const previous = registry.get(token);
  if (previous && previous.observer) previous.observer.disconnect();

  const stableId = (element) => {
    let current = element;
    for (let depth = 0; current && depth < 7; depth += 1, current = current.parentElement) {
      for (const attribute of Array.from(current.attributes || [])) {
        const name = String(attribute.name || "").toLowerCase();
        if (!/(message|msg).*(id)|id.*(message|msg)/.test(name)) continue;
        const value = String(attribute.value || "").trim();
        if (value && value !== "0" && value !== "undefined" && value !== "null") {
          return name + ":" + value;
        }
      }
    }
    return "";
  };

  const matchesText = (element) => {
    const value = normalize(element.innerText || element.textContent || "");
    if (value === wanted) return true;
    if (!value || value.length > wanted.length + 80) return false;
    const offset = value.indexOf(wanted);
    if (offset < 0) return false;
    const remainder = normalize(value.slice(0, offset) + " " + value.slice(offset + wanted.length));
    return !remainder || /^(?:[·•|｜:\-—,，。\s]|\d{1,2}:\d{2}|刚刚|发送中|已发送|已送达|发送失败|重试)+$/.test(remainder);
  };

  const messageContainer = (element) => {
    const stable = element.closest([
      "[data-message-id]", "[data-msg-id]", "[data-msgid]",
      "[data-client-message-id]", "[data-server-message-id]",
      '[role="listitem"]', "li"
    ].join(","));
    if (stable && root.contains(stable)) return stable;
    let current = element;
    for (let depth = 0; current && current !== root && depth < 5; depth += 1) {
      const marker = String(current.className || "") + " " + String(current.getAttribute("data-testid") || "");
      if (/(message|msg|bubble)/i.test(marker) && !/(editor|input)/i.test(marker)) return current;
      current = current.parentElement;
    }
    return element;
  };

  const outgoingHint = (container, textElement) => {
    let current = container;
    for (let depth = 0; current && current !== root && depth < 7; depth += 1) {
      const marker = [
        current.className || "",
        current.getAttribute("data-testid") || "",
        current.getAttribute("data-direction") || "",
        current.getAttribute("data-owner") || "",
        current.getAttribute("data-is-self") || "",
        current.getAttribute("data-self") || ""
      ].join(" ").toLowerCase();
      if (/(^|[\s_-])(incoming|received|other|left)([\s_-]|$)/.test(marker)) return false;
      if (/(^|[\s_-])(outgoing|sent|mine|self|right)([\s_-]|$)/.test(marker) || /(?:is-self|from-me|message-right)/.test(marker)) return true;
      current = current.parentElement;
    }
    const rectangle = textElement.getBoundingClientRect();
    if (!rectangle.width || !rectangle.height) return false;
    const editor = root.querySelector(editorSelector);
    const reference = editor && editor.getBoundingClientRect().width
      ? editor.getBoundingClientRect()
      : root.getBoundingClientRect();
    return rectangle.left + rectangle.width * 0.5 >= reference.left + reference.width * 0.5;
  };

  const fingerprint = (container, textElement, id) => {
    if (id) return "id:" + id;
    const parts = [];
    let current = container;
    for (let depth = 0; current && current !== root && depth < 5; depth += 1) {
      const parent = current.parentElement;
      const index = parent ? Array.prototype.indexOf.call(parent.children, current) : -1;
      const semanticClass = String(current.className || "")
        .split(/\s+/)
        .filter((name) => /(message|msg|bubble|item|right|self|mine|sent)/i.test(name))
        .slice(0, 3)
        .join(".");
      parts.push(current.tagName + ":" + semanticClass + ":" + index);
      current = parent;
    }
    const rectangle = textElement.getBoundingClientRect();
    return "dom:" + parts.join("/") + ":" + Math.round(rectangle.top / 4);
  };

  const candidates = () => Array.from(root.querySelectorAll("div, span, p, pre, li, section, article"))
    .filter((element) => !element.closest(editorSelector) && matchesText(element))
    .filter((element) => !Array.from(element.children).some((child) => matchesText(child)))
    .map((textElement) => {
      const container = messageContainer(textElement);
      const id = stableId(container);
      return {
        container,
        textElement,
        id,
        fingerprint: fingerprint(container, textElement, id),
        outgoing: outgoingHint(container, textElement)
      };
    })
    .filter((candidate) => candidate.outgoing);

  const state = {
    observer: null,
    baselineNodes: new WeakSet(),
    baselineIds: new Set(),
    baselineFingerprints: new Set(),
    addedRoots: [],
    // Virtualized chat lists can recycle an existing message row instead of
    // inserting a new node. Keep the actual text/character mutation targets
    // separately so a reused row can still be correlated without treating a
    // status-icon class change as a new message.
    touchedNodes: [],
    attributeTargets: [],
    result: null,
    scan: null
  };

  for (const candidate of candidates()) {
    state.baselineNodes.add(candidate.container);
    if (candidate.id) state.baselineIds.add(candidate.id);
    state.baselineFingerprints.add(candidate.fingerprint);
  }

  const wasAdded = (candidate) => state.addedRoots.some((added) => {
    if (!(added instanceof Node) || !added.isConnected) return false;
    return added === candidate.container || added.contains(candidate.container) || candidate.container.contains(added);
  });

  const wasContentTouched = (candidate) => state.touchedNodes.some((touched) => {
    if (!(touched instanceof Node)) return false;
    if (touched === candidate.textElement || touched === candidate.container) return true;
    // For characterData, the MutationRecord target is the text node. For a
    // childList replacement it is commonly the text element's parent.
    return candidate.textElement.contains(touched) ||
      candidate.container.contains(touched) && touched.nodeType === Node.TEXT_NODE;
  }) || state.attributeTargets.some(({ node }) =>
    node === candidate.textElement ||
    node === candidate.container ||
    candidate.textElement.contains(node)
  );

  const nodeHasWantedText = (node) => {
    if (!(node instanceof Node)) return false;
    const value = normalize(
      node.nodeType === Node.TEXT_NODE
        ? node.nodeValue
        : node.innerText || node.textContent || ""
    );
    if (value === wanted) return true;
    if (!value || value.length > wanted.length + 80) return false;
    const offset = value.indexOf(wanted);
    if (offset < 0) return false;
    const remainder = normalize(value.slice(0, offset) + " " + value.slice(offset + wanted.length));
    return !remainder || /^(?:[·•|｜:\-—,，。\s]|\d{1,2}:\d{2}|刚刚|发送中|已发送|已送达|发送失败|重试)+$/.test(remainder);
  };

  state.scan = () => {
    if (state.result) return state.result;
    for (const candidate of candidates()) {
      if (candidate.id && !state.baselineIds.has(candidate.id)) {
        state.result = { source: "dom", reason: "new_message_id", messageId: candidate.id };
        break;
      }
      if (!state.baselineNodes.has(candidate.container) && wasAdded(candidate)) {
        state.result = { source: "dom", reason: "new_outgoing_node", messageId: candidate.id || null };
        break;
      }
      if (!state.baselineFingerprints.has(candidate.fingerprint) && wasAdded(candidate)) {
        state.result = { source: "dom", reason: "new_outgoing_fingerprint", messageId: candidate.id || null };
        break;
      }
      if (state.baselineNodes.has(candidate.container) && wasContentTouched(candidate)) {
        state.result = { source: "dom", reason: "changed_outgoing_text_node", messageId: candidate.id || null };
        break;
      }
    }
    return state.result;
  };

  state.observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === "childList") {
        for (const node of mutation.addedNodes) {
          state.addedRoots.push(node.nodeType === Node.TEXT_NODE ? node.parentElement : node);
        }
        // A virtualized row is often updated via childList replacement. Only
        // remember it as content-related when the inserted/removed fragment
        // actually contains this exact message; status-icon mounts should not
        // turn an already-visible bubble into a fresh confirmation.
        const contentChanged = [
          ...Array.from(mutation.addedNodes || []),
          ...Array.from(mutation.removedNodes || []),
        ].some(nodeHasWantedText);
        if (contentChanged) {
          state.touchedNodes.push(mutation.target);
          for (const node of mutation.addedNodes) {
            if (nodeHasWantedText(node)) state.touchedNodes.push(node);
          }
        }
      } else if (mutation.type === "characterData") {
        // Character data changes are relevant only once the surrounding node
        // now matches this exact message. This filters timestamp/status text
        // changes in an already-rendered bubble.
        const parent = mutation.target && mutation.target.parentElement;
        if (nodeHasWantedText(mutation.target) || nodeHasWantedText(parent)) {
          state.touchedNodes.push(mutation.target);
        }
      } else if (mutation.type === "attributes") {
        // Only the candidate row/text element itself is considered below.
        // This lets a recycled row become outgoing after a data/class update
        // without allowing an unrelated status icon mutation to confirm it.
        state.attributeTargets.push({
          node: mutation.target,
          name: mutation.attributeName || "",
        });
      }
    }
    if (state.addedRoots.length > 256) state.addedRoots.splice(0, state.addedRoots.length - 256);
    if (state.touchedNodes.length > 256) state.touchedNodes.splice(0, state.touchedNodes.length - 256);
    if (state.attributeTargets.length > 256) state.attributeTargets.splice(0, state.attributeTargets.length - 256);
    state.scan();
  });
  state.observer.observe(root, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: [
      "class", "data-message-id", "data-msg-id", "data-msgid",
      "data-client-message-id", "data-server-message-id", "data-direction",
      "data-owner", "data-is-self", "data-self"
    ]
  });
  registry.set(token, state);

  return {
    installed: true,
    baselineIds: state.baselineIds.size,
    baselineFingerprints: state.baselineFingerprints.size
  };
}
"""


_WAIT_FOR_MESSAGE_CONFIRMATION_SCRIPT = r"""
(token) => {
  const registry = window.__sparkMessageConfirmationV2;
  const state = registry && registry.get(token);
  if (!state) return false;
  return state.result || state.scan() || false;
}
"""


_WAIT_FOR_LEGACY_MESSAGE_CONFIRMATION_SCRIPT = r"""
({ message, baseline }) => {
  const normalize = (value) => String(value || "")
    .replace(/[\u200B-\u200D\u2060\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const wanted = normalize(message);
  const root = document.querySelector("#sub-app") || document.body;
  if (!root || !wanted) return false;
  const editor = root.querySelector('[contenteditable="true"], textarea, [class*="chat-input-"]');
  const matches = Array.from(root.querySelectorAll("div, span, p, pre, li, section, article"))
    .filter((element) => !editor || !editor.contains(element))
    .filter((element) => normalize(element.innerText || element.textContent) === wanted)
    .filter((element) => !Array.from(element.children).some((child) => normalize(child.innerText || child.textContent) === wanted));
  return matches.length > Number(baseline || 0);
}
"""


_DISPOSE_MESSAGE_CONFIRMATION_OBSERVER_SCRIPT = r"""
(token) => {
  const registry = window.__sparkMessageConfirmationV2;
  if (!registry) return false;
  const state = registry.get(token);
  if (state && state.observer) state.observer.disconnect();
  registry.delete(token);
  return true;
}
"""


_MESSAGE_CONTENT_KEYS = {
    "content",
    "message",
    "messagetext",
    "msgcontent",
    "msgtext",
    "text",
}
_CLIENT_MESSAGE_ID_KEYS = {
    "clientid",
    "clientmessageid",
    "clientmsgid",
    "localid",
    "localmessageid",
    "localmsgid",
}
_SERVER_MESSAGE_ID_KEYS = {
    "messageid",
    "msgid",
    "serverid",
    "servermessageid",
    "servermsgid",
}
_SUCCESS_CODE_KEYS = {"code", "errcode", "errorcode", "statuscode"}
_SUCCESS_BOOL_KEYS = {"ok", "success", "succeeded"}
_SUCCESS_STATUS_KEYS = {"result", "status"}
_FAILURE_KEYS = {"error", "errormessage", "errmsg", "error_message"}
_OFFICIAL_IM_HOST_SUFFIXES = (
    "douyin.com",
    "iesdouyin.com",
    "amemv.com",
    "snssdk.com",
)
_MISSING_PLAYWRIGHT_VALUE = object()


@dataclass(frozen=True)
class MessageConfirmationEvidence:
    source: str
    reason: str
    message_id: str | None = None


@dataclass
class MessageConfirmationProbe:
    token: str
    message: str = field(repr=False)
    dom_available: bool = False
    network_monitor: "MessageNetworkAckMonitor | None" = field(
        default=None, repr=False
    )


def _normalize_confirmation_text(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"[\u200B-\u200D\u2060\uFEFF]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalized_payload_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _decode_structured_payload(value: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text[:1] in {"{", "["}:
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return None
    try:
        parsed = parse_qs(text, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return None
    return parsed or None


def _iter_payload_objects(value: object, depth: int = 0):
    if depth > 10:
        return
    structured = _decode_structured_payload(value)
    if structured is not None and structured is not value:
        yield from _iter_payload_objects(structured, depth + 1)
        return
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                yield from _iter_payload_objects(nested, depth + 1)
            elif isinstance(nested, str) and nested.strip()[:1] in {"{", "["}:
                yield from _iter_payload_objects(nested, depth + 1)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_payload_objects(nested, depth + 1)


def _payload_contains_exact_message(payload: object, message: str) -> bool:
    wanted = _normalize_confirmation_text(message)
    if not wanted:
        return False

    # A few Douyin builds put the message body directly in a protobuf string
    # or query parameter instead of wrapping it in a ``text``/``content``
    # object.  Checking scalar values here makes the correlation tolerant of
    # that transport detail while the caller still limits the check to an
    # official message-send request.
    raw_bytes = payload if isinstance(payload, bytes) else None
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return _protobuf_payload_contains_exact_message(payload, wanted)
    if isinstance(payload, str):
        if _normalize_confirmation_text(payload) == wanted:
            return True
        structured = _decode_structured_payload(payload)
        if structured is not None and structured is not payload:
            return _payload_contains_exact_message(structured, message)
    elif isinstance(payload, (list, tuple, set)):
        if any(_payload_contains_exact_message(item, message) for item in payload):
            return True

    for mapping in _iter_payload_objects(payload):
        for key, value in mapping.items():
            if _normalized_payload_key(key) not in _MESSAGE_CONTENT_KEYS:
                continue
            if _normalize_confirmation_text(value) == wanted:
                return True
            if isinstance(value, (list, tuple, set)) and any(
                _normalize_confirmation_text(item) == wanted for item in value
            ):
                return True
            nested = _decode_structured_payload(value)
            if nested is not None and nested is not value:
                if _payload_contains_exact_message(nested, message):
                    return True
    if raw_bytes is not None:
        return _protobuf_payload_contains_exact_message(raw_bytes, wanted)
    return False


def _protobuf_payload_contains_exact_message(payload: bytes, wanted: str) -> bool:
    """Find an exact UTF-8 message in a protobuf payload.

    The web chat endpoint has changed its envelope more than once.  We keep
    the strict decoder for the known envelope, but use this conservative
    recursive fallback when the envelope gains an extra wrapper field.  Only
    length-delimited UTF-8 strings are inspected; arbitrary binary fields are
    never treated as text.
    """
    try:
        fields = list(_iter_protobuf_fields(payload))
    except _ProtobufDecodeError:
        return False
    for field_number, wire_type, value in fields:
        if wire_type != 2 or not isinstance(value, bytes):
            continue
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        if text and _normalize_confirmation_text(text) == wanted:
            return True
        if text and _payload_contains_exact_message(text, wanted):
            return True
        if _protobuf_payload_contains_exact_message(value, wanted):
            return True
    return False


def _payload_ids(payload: object, keys: set[str]) -> set[str]:
    values: set[str] = set()
    for mapping in _iter_payload_objects(payload):
        for key, value in mapping.items():
            if _normalized_payload_key(key) not in keys:
                continue
            candidates = value if isinstance(value, (list, tuple, set)) else (value,)
            for candidate in candidates:
                text = str(candidate or "").strip()
                if text and text not in {"0", "None", "null", "undefined"}:
                    values.add(text)
    return values


def _coerce_status_code(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _payload_has_explicit_success(payload: object) -> bool:
    positive = False
    negative = False
    for mapping in _iter_payload_objects(payload):
        for key, value in mapping.items():
            normalized_key = _normalized_payload_key(key)
            candidates = value if isinstance(value, (list, tuple, set)) else (value,)
            for candidate in candidates:
                if normalized_key in _SUCCESS_CODE_KEYS:
                    code = _coerce_status_code(candidate)
                    if code is not None:
                        positive = positive or code == 0
                        negative = negative or code != 0
                elif normalized_key in _SUCCESS_BOOL_KEYS and isinstance(
                    candidate, bool
                ):
                    positive = positive or candidate
                    negative = negative or not candidate
                elif normalized_key in _SUCCESS_STATUS_KEYS and isinstance(
                    candidate, str
                ):
                    status = candidate.strip().lower()
                    if status in {"ok", "success", "succeeded"}:
                        positive = True
                    elif status in {"error", "failed", "failure", "rejected"}:
                        negative = True
                elif normalized_key in _SUCCESS_STATUS_KEYS:
                    code = _coerce_status_code(candidate)
                    if code is not None:
                        positive = positive or code == 0
                        negative = negative or code != 0
                elif normalized_key in _FAILURE_KEYS:
                    if isinstance(candidate, bool):
                        negative = negative or candidate
                    elif candidate not in (None, "", [], {}, ()):  # error detail
                        negative = True
    return positive and not negative


def _is_strict_message_send_url(value: object) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == "imapi.douyin.com"
        and parsed.path.rstrip("/") == "/v1/message/send"
    )


def _is_candidate_message_send_url(value: object) -> bool:
    """Recognize official web-chat send endpoints across Douyin web builds.

    The exact ``imapi.douyin.com/v1/message/send`` route is still preferred,
    but the web app has also routed sends through versioned and creator-side
    paths.  We only broaden the URL check within official Douyin-owned hosts
    and require both a message/IM marker and a send-like operation marker.
    Payload correlation is performed separately and is mandatory.
    """
    if _is_strict_message_send_url(value):
        return True
    try:
        parsed = urlparse(str(value or ""))
    except (TypeError, ValueError):
        return False
    if parsed.scheme != "https":
        return False
    hostname = (parsed.hostname or "").lower()
    if not any(
        hostname == suffix or hostname.endswith("." + suffix)
        for suffix in _OFFICIAL_IM_HOST_SUFFIXES
    ):
        return False
    path = (parsed.path or "").lower()
    operation = path + "?" + (parsed.query or "").lower()
    has_message_marker = any(
        marker in operation for marker in ("message", "msg", "/im/", "im_", "im-")
    )
    has_send_marker = any(
        marker in operation
        for marker in ("send", "publish", "create_message", "sendmsg")
    )
    return has_message_marker and has_send_marker


def _is_official_im_websocket_url(value: object) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except (TypeError, ValueError):
        return False
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme in {"ws", "wss"}
        and any(
            hostname == suffix or hostname.endswith("." + suffix)
            for suffix in _OFFICIAL_IM_HOST_SUFFIXES
        )
        and any(
            marker in parsed.path.lower()
            for marker in ("/im", "/ws", "websocket")
        )
    )


def _payload_has_send_semantics(payload: object) -> bool:
    semantic_keys = {"action", "command", "event", "method", "operation", "type"}
    for mapping in _iter_payload_objects(payload):
        for key, value in mapping.items():
            if _normalized_payload_key(key) not in semantic_keys:
                continue
            marker = _normalize_confirmation_text(value).lower()
            if "send" in marker and (
                "message" in marker or "msg" in marker or marker == "send"
            ):
                return True
    return False


async def _read_playwright_value(obj, name: str, default=None):
    try:
        value = getattr(obj, name)
        if callable(value):
            value = value()
        if inspect.isawaitable(value):
            value = await value
        return value
    except Exception:
        return default


class _ProtobufDecodeError(ValueError):
    pass


def _read_protobuf_varint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(payload):
            raise _ProtobufDecodeError("truncated varint")
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
    raise _ProtobufDecodeError("varint exceeds 10 bytes")


def _iter_protobuf_fields(payload: bytes):
    offset = 0
    while offset < len(payload):
        key, offset = _read_protobuf_varint(payload, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number <= 0:
            raise _ProtobufDecodeError("invalid field number")
        if wire_type == 0:
            value, offset = _read_protobuf_varint(payload, offset)
        elif wire_type == 1:
            if offset + 8 > len(payload):
                raise _ProtobufDecodeError("truncated fixed64")
            value = payload[offset : offset + 8]
            offset += 8
        elif wire_type == 2:
            length, offset = _read_protobuf_varint(payload, offset)
            if length < 0 or offset + length > len(payload):
                raise _ProtobufDecodeError("truncated length-delimited field")
            value = payload[offset : offset + length]
            offset += length
        elif wire_type == 5:
            if offset + 4 > len(payload):
                raise _ProtobufDecodeError("truncated fixed32")
            value = payload[offset : offset + 4]
            offset += 4
        else:
            raise _ProtobufDecodeError("unsupported protobuf wire type")
        yield field_number, wire_type, value


def _single_protobuf_field(
    payload: bytes, field_number: int, wire_type: int
) -> object:
    matches = [
        value
        for number, candidate_wire_type, value in _iter_protobuf_fields(payload)
        if number == field_number and candidate_wire_type == wire_type
    ]
    if len(matches) != 1:
        raise _ProtobufDecodeError("required singular field was absent or repeated")
    return matches[0]


def _decode_protobuf_string(value: object) -> str:
    if not isinstance(value, bytes):
        raise _ProtobufDecodeError("string field was not length-delimited")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _ProtobufDecodeError("string field was not utf-8") from error


def _parse_douyin_send_request(payload: object, message: str) -> str | None:
    if not isinstance(payload, bytes) or not payload:
        return None
    try:
        body = _single_protobuf_field(payload, 8, 2)
        if not isinstance(body, bytes):
            return None
        content_message = _single_protobuf_field(body, 100, 2)
        if not isinstance(content_message, bytes):
            return None
        content = _decode_protobuf_string(
            _single_protobuf_field(content_message, 4, 2)
        )
        client_message_id = _decode_protobuf_string(
            _single_protobuf_field(content_message, 8, 2)
        ).strip()
    except _ProtobufDecodeError:
        return None
    if not client_message_id or not _payload_contains_exact_message(content, message):
        return None
    return client_message_id


def _parse_douyin_send_request_details(
    payload: object, message: str
) -> tuple[bool, str | None]:
    """Return whether a request carries this exact message and its client ID.

    ``_parse_douyin_send_request`` intentionally remains strict for callers
    that need the known protobuf envelope.  The browser monitor needs a little
    more tolerance: recent web builds have used JSON/query envelopes and have
    occasionally omitted the client ID from the outer response.  The boolean
    is therefore the primary correlation signal and the ID is optional.
    """
    if not payload:
        return False, None

    strict_client_id = _parse_douyin_send_request(payload, message)
    if strict_client_id:
        return True, strict_client_id

    structured = _decode_structured_payload(payload)
    if structured is not None and _payload_contains_exact_message(structured, message):
        client_ids = _payload_ids(structured, _CLIENT_MESSAGE_ID_KEYS)
        return True, sorted(client_ids)[0] if client_ids else None

    if isinstance(payload, bytes):
        wanted = _normalize_confirmation_text(message)
        if _protobuf_payload_contains_exact_message(payload, wanted):
            # The fallback envelope is intentionally not guessed here.  A
            # missing client ID is safe to handle only when the response is
            # tied to this exact request and a positive status is observed.
            return True, None
    return False, None


def _parse_douyin_send_response(
    payload: object, expected_client_message_id: str
) -> tuple[str, str] | None:
    if not isinstance(payload, bytes) or not payload or not expected_client_message_id:
        return None
    try:
        error_code = _single_protobuf_field(payload, 3, 0)
        status_message = _decode_protobuf_string(
            _single_protobuf_field(payload, 4, 2)
        )
        message_data = _single_protobuf_field(payload, 6, 2)
        if not isinstance(message_data, bytes):
            return None
        message_info = _single_protobuf_field(message_data, 100, 2)
        if not isinstance(message_info, bytes):
            return None
        message_id = _single_protobuf_field(message_info, 1, 0)
        client_message_id = _decode_protobuf_string(
            _single_protobuf_field(message_info, 4, 2)
        ).strip()
    except _ProtobufDecodeError:
        return None
    if (
        error_code != 0
        or status_message.strip().lower() not in {"ok", "success", "succeeded"}
        or not isinstance(message_id, int)
        or message_id <= 0
        or client_message_id != expected_client_message_id
    ):
        return None
    return str(message_id), client_message_id


def _protobuf_response_confirms_send(
    payload: object, expected_client_message_id: str | None = None
) -> tuple[bool, str | None, str]:
    """Tolerate an additional protobuf response envelope.

    The strict decoder above handles the known IM response schema.  This
    fallback only accepts a binary response when it contains an explicit
    success marker (or the known zero error-code field), and—when a request
    client ID is available—also contains that ID.  It never treats an
    arbitrary HTTP 200 body as delivery confirmation.
    """
    if not isinstance(payload, bytes) or not payload:
        return False, None, "unrecognized_protobuf_send_response"
    try:
        top_level = list(_iter_protobuf_fields(payload))
    except _ProtobufDecodeError:
        return False, None, "unrecognized_protobuf_send_response"

    strings: list[str] = []
    server_ids: list[int] = []
    success_marker = False
    failure_marker = False
    zero_error_code = False

    def walk(fields: list[tuple[int, int, object]], depth: int = 0) -> None:
        nonlocal failure_marker, success_marker, zero_error_code
        if depth > 10:
            return
        for field_number, wire_type, value in fields:
            if wire_type == 0 and isinstance(value, int):
                if field_number == 3 and value == 0:
                    zero_error_code = True
                if field_number == 1 and value > 0:
                    server_ids.append(value)
                continue
            if wire_type != 2 or not isinstance(value, bytes):
                continue
            try:
                text = value.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if text:
                strings.append(text.strip())
                if text.strip().lower() in {"ok", "success", "succeeded"}:
                    success_marker = True
                if text.strip().lower() in {
                    "error",
                    "failed",
                    "failure",
                    "rejected",
                }:
                    failure_marker = True
            try:
                nested = list(_iter_protobuf_fields(value))
            except _ProtobufDecodeError:
                continue
            walk(nested, depth + 1)

    walk(top_level)
    if expected_client_message_id and expected_client_message_id not in strings:
        return False, None, "protobuf_send_response_client_id_missing"
    if failure_marker or not (success_marker or zero_error_code):
        return False, None, "protobuf_send_response_not_success"
    return (
        True,
        str(server_ids[0]) if server_ids else None,
        "correlated_protobuf_send_response_fallback",
    )


def _response_payload_confirms_send(
    payload: object,
    *,
    expected_client_message_id: str | None = None,
) -> tuple[bool, str | None, str]:
    """Interpret a successful JSON/query ACK without trusting page text.

    Returns ``(confirmed, server_id, reason)``.  An empty body is accepted by
    the caller only after the HTTP status and exact outgoing request have been
    checked; a non-empty body must expose an explicit success marker.  This
    prevents unrelated 200 responses from turning into false delivery
    confirmations.
    """
    if payload in (b"", ""):
        return True, None, "http_send_response_empty_body"

    structured = _decode_structured_payload(payload)
    if structured is None:
        return False, None, "unrecognized_send_response"
    if not _payload_has_explicit_success(structured):
        return False, None, "send_response_not_success"

    response_client_ids = _payload_ids(structured, _CLIENT_MESSAGE_ID_KEYS)
    if (
        expected_client_message_id
        and response_client_ids
        and expected_client_message_id not in response_client_ids
    ):
        return False, None, "send_response_client_id_mismatch"
    server_ids = _payload_ids(structured, _SERVER_MESSAGE_ID_KEYS)
    return (
        True,
        sorted(server_ids)[0] if server_ids else None,
        "correlated_json_send_response",
    )


class MessageNetworkAckMonitor:
    """Collect only ACKs that can be tied to this exact outgoing message."""

    def __init__(self, message: str):
        self.message = message
        self._page = None
        self._armed = False
        self._event = asyncio.Event()
        self._evidence: MessageConfirmationEvidence | None = None
        self._pending_client_ids: set[str] = set()
        self._pending_send_without_id = False
        self._tasks: set[asyncio.Task] = set()
        self._websockets: list[tuple[object, object, object]] = []

    @property
    def available(self) -> bool:
        return self._page is not None

    def attach(self, page) -> None:
        if self._page is page:
            return
        on = getattr(page, "on", None)
        if not callable(on):
            return
        try:
            on("response", self._capture_response)
            on("websocket", self._capture_websocket)
        except Exception:
            return
        self._page = page

    def arm(self) -> None:
        self._pending_client_ids.clear()
        self._pending_send_without_id = False
        self._evidence = None
        self._event.clear()
        self._armed = True

    def _schedule(self, awaitable) -> None:
        try:
            task = asyncio.create_task(awaitable)
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _capture_response(self, response) -> None:
        if not self._armed or not _is_candidate_message_send_url(
            getattr(response, "url", "")
        ):
            return
        self._schedule(self.handle_response(response))

    def _capture_websocket(self, websocket) -> None:
        if not _is_official_im_websocket_url(getattr(websocket, "url", "")):
            return
        on = getattr(websocket, "on", None)
        if not callable(on):
            return

        def sent(frame) -> None:
            if self._armed:
                self.handle_websocket_sent(frame)

        def received(frame) -> None:
            if self._armed:
                self.handle_websocket_received(frame)

        try:
            on("framesent", sent)
            on("framereceived", received)
        except Exception:
            return
        self._websockets.append((websocket, sent, received))

    async def handle_response(self, response) -> None:
        if not self._armed or not _is_candidate_message_send_url(
            await _read_playwright_value(response, "url", "")
        ):
            return
        status = _coerce_status_code(
            await _read_playwright_value(response, "status", None)
        )
        if status is None or status < 200 or status >= 300:
            return
        request = await _read_playwright_value(response, "request", None)
        if request is None:
            return
        method = str(await _read_playwright_value(request, "method", "")).upper()
        if method != "POST":
            return
        request_payload = await _read_playwright_value(
            request, "post_data_buffer", None
        )
        if request_payload is None:
            request_payload = await _read_playwright_value(
                request, "post_data_json", None
            )
        if request_payload is None:
            request_payload = await _read_playwright_value(request, "post_data", None)
        request_matches, client_message_id = _parse_douyin_send_request_details(
            request_payload, self.message
        )
        if not request_matches:
            return
        response_payload = await _read_playwright_value(
            response, "body", _MISSING_PLAYWRIGHT_VALUE
        )
        if response_payload is _MISSING_PLAYWRIGHT_VALUE:
            # A body read failure is not an ACK.  The request crossed the
            # submit boundary already, so the executor will retain its
            # at-most-once ``submitted_unconfirmed`` state rather than retry.
            return
        if client_message_id:
            confirmation = _parse_douyin_send_response(
                response_payload, client_message_id
            )
            if confirmation is not None:
                message_id, _ = confirmation
                self._confirm(
                    MessageConfirmationEvidence(
                        "network_response",
                        "correlated_protobuf_send_response",
                        message_id,
                    )
                )
                return
        confirmed, message_id, reason = _response_payload_confirms_send(
            response_payload,
            expected_client_message_id=client_message_id,
        )
        if not confirmed:
            confirmed, message_id, reason = _protobuf_response_confirms_send(
                response_payload,
                expected_client_message_id=client_message_id,
            )
        if not confirmed:
            return
        self._confirm(
            MessageConfirmationEvidence(
                "network_response",
                reason,
                message_id,
            )
        )

    def handle_websocket_sent(self, frame: object) -> None:
        payload = _decode_structured_payload(frame)
        if (
            not self._armed
            or payload is None
            or not _payload_contains_exact_message(payload, self.message)
        ):
            return
        client_ids = _payload_ids(payload, _CLIENT_MESSAGE_ID_KEYS)
        if not _payload_has_send_semantics(payload) and not client_ids:
            return
        self._pending_client_ids.update(
            client_ids
        )
        if not client_ids:
            self._pending_send_without_id = True

    def handle_websocket_received(self, frame: object) -> None:
        if not self._armed or (
            not self._pending_client_ids and not self._pending_send_without_id
        ):
            return
        payload = _decode_structured_payload(frame)
        if payload is None or not _payload_has_explicit_success(payload):
            return
        response_client_ids = _payload_ids(payload, _CLIENT_MESSAGE_ID_KEYS)
        if self._pending_client_ids and not self._pending_client_ids.intersection(
            response_client_ids
        ):
            return
        server_ids = _payload_ids(payload, _SERVER_MESSAGE_ID_KEYS)
        self._confirm(
            MessageConfirmationEvidence(
                "network_websocket",
                "correlated_websocket_ack",
                sorted(server_ids)[0] if server_ids else None,
            )
        )

    def _confirm(self, evidence: MessageConfirmationEvidence) -> None:
        if self._evidence is not None:
            return
        self._evidence = evidence
        self._event.set()

    async def wait(self) -> MessageConfirmationEvidence:
        await self._event.wait()
        if self._evidence is None:
            raise RuntimeError("network acknowledgement event had no evidence")
        return self._evidence

    def detach(self) -> None:
        self._armed = False
        page = self._page
        self._page = None
        remove = getattr(page, "remove_listener", None)
        if callable(remove):
            try:
                remove("response", self._capture_response)
                remove("websocket", self._capture_websocket)
            except Exception:
                pass
        for websocket, sent, received in self._websockets:
            remove_ws = getattr(websocket, "remove_listener", None)
            if callable(remove_ws):
                try:
                    remove_ws("framesent", sent)
                    remove_ws("framereceived", received)
                except Exception:
                    pass
        self._websockets.clear()
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()
        self._tasks.clear()


class LoginRequiredError(RuntimeError):
    """Raised when Douyin redirects an account back to the login page."""


async def wait_for_first_visible(page, selectors, timeout=15000):
    """Return the first selector that becomes visible."""
    last_error = None
    for selector in selectors:
        try:
            await page.wait_for_selector(selector, state="visible", timeout=timeout)
            return selector
        except Exception as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise ValueError("selectors must not be empty")


async def page_has_login_prompt(page):
    """Return whether the current page is asking the user to log in again."""
    if "/creator-micro/data/following/chat" not in page.url:
        return True
    for selector in ("text=扫码登录", "text=手机号登录", "text=验证码登录"):
        if await page.locator(selector).count() > 0:
            return True
    return False


async def wait_for_authenticated_selector(
    page, selectors, timeout=15000, poll_timeout=1000
):
    """Wait for a page control while continuously checking for a late login redirect."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout / 1000
    last_error = None

    while loop.time() < deadline:
        if await page_has_login_prompt(page):
            raise LoginRequiredError("Douyin login is required")

        for selector in selectors:
            remaining_ms = max(1, int((deadline - loop.time()) * 1000))
            if remaining_ms <= 1:
                break
            try:
                await page.wait_for_selector(
                    selector,
                    state="visible",
                    timeout=min(poll_timeout, remaining_ms),
                )
                return selector
            except Exception as error:
                last_error = error
                if await page_has_login_prompt(page):
                    raise LoginRequiredError("Douyin login is required") from error

    if last_error is not None:
        raise last_error
    raise TimeoutError("timed out waiting for an authenticated page control")


async def click_first_match(page, selector):
    """Click the first element when a selector intentionally matches many."""
    await page.locator(selector).first.click()


async def capture_message_confirmation_baseline(
    page,
    message,
    *,
    network_monitor: MessageNetworkAckMonitor | None = None,
):
    """Install pre-submit DOM and network observers for one exact message."""
    verification_lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not verification_lines:
        raise ValueError("message must contain visible text")

    monitor = network_monitor or MessageNetworkAckMonitor(message)
    monitor.attach(page)
    token = uuid.uuid4().hex
    dom_available = False
    try:
        state = await page.evaluate(
            _INSTALL_MESSAGE_CONFIRMATION_OBSERVER_SCRIPT,
            {"token": token, "message": message},
        )
        dom_available = bool(
            isinstance(state, dict) and state.get("installed") is True
        )
    except Exception as error:
        logger.warning(
            "message DOM confirmation observer unavailable exception=%s",
            type(error).__name__,
        )
    monitor.arm()
    return MessageConfirmationProbe(
        token=token,
        message=message,
        dom_available=dom_available,
        network_monitor=monitor,
    )


async def _confirmation_handle_value(handle) -> object:
    json_value = getattr(handle, "json_value", None)
    if callable(json_value):
        value = json_value()
        if inspect.isawaitable(value):
            return await value
        return value
    return handle


async def _wait_for_dom_confirmation(
    page, probe: MessageConfirmationProbe, timeout: int
) -> MessageConfirmationEvidence:
    handle = await page.wait_for_function(
        _WAIT_FOR_MESSAGE_CONFIRMATION_SCRIPT,
        arg=probe.token,
        timeout=timeout,
    )
    value = await _confirmation_handle_value(handle)
    if not isinstance(value, dict) or value.get("source") != "dom":
        raise RuntimeError("DOM confirmation returned no correlated evidence")
    return MessageConfirmationEvidence(
        source="dom",
        reason=str(value.get("reason") or "new_outgoing_message"),
        message_id=str(value["messageId"]) if value.get("messageId") else None,
    )


async def _wait_for_first_confirmation(
    awaitables: list[object], timeout: int
) -> MessageConfirmationEvidence:
    if not awaitables:
        raise RuntimeError("no message confirmation signal is available")
    tasks = {asyncio.create_task(awaitable) for awaitable in awaitables}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1, timeout) / 1000
    last_error: BaseException | None = None
    try:
        while tasks:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("message confirmation timed out")
            done, tasks = await asyncio.wait(
                tasks,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError("message confirmation timed out")
            for task in done:
                try:
                    evidence = task.result()
                except BaseException as error:
                    last_error = error
                    continue
                if isinstance(evidence, MessageConfirmationEvidence):
                    return evidence
        if last_error is not None:
            raise RuntimeError("all message confirmation signals failed") from last_error
        raise TimeoutError("message confirmation timed out")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def dispose_message_confirmation_probe(page, probe: object) -> None:
    if not isinstance(probe, MessageConfirmationProbe):
        return
    if probe.network_monitor is not None:
        probe.network_monitor.detach()
    if not probe.dom_available:
        return
    try:
        await page.evaluate(
            _DISPOSE_MESSAGE_CONFIRMATION_OBSERVER_SCRIPT,
            probe.token,
        )
    except Exception:
        logger.debug("message confirmation observer cleanup was unavailable")


async def confirm_message_sent(
    page,
    chat_input,
    message,
    timeout=45000,
    baseline_count=None,
):
    """Confirm from a strict network ACK or a new outgoing DOM node.

    Editor clearing is deliberately only a secondary observation. Douyin can
    replace its contenteditable node after a successful send, so holding the
    pre-send element handle and waiting on ``innerText`` produced false
    negatives even when the message was already visible in the phone app.
    """
    verification_lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not verification_lines:
        raise ValueError("message must contain visible text")

    probe = baseline_count
    if isinstance(probe, int) and not isinstance(probe, bool):
        # Compatibility for the original public helper contract.  New code
        # passes a MessageConfirmationProbe, but older callers may still have
        # captured a numeric DOM baseline.  This path intentionally remains a
        # confirmation-only fallback; it never changes the executor's
        # at-most-once submission policy.
        await page.wait_for_function(
            _WAIT_FOR_LEGACY_MESSAGE_CONFIRMATION_SCRIPT,
            arg={"message": message, "baseline": probe},
            timeout=timeout,
        )
        try:
            input_handle = await chat_input.element_handle()
            if input_handle is not None:
                await page.wait_for_function(
                    "(element) => !String(element.innerText || element.textContent || '').trim()",
                    arg=input_handle,
                    timeout=min(timeout, 1500),
                )
        except Exception:
            logger.debug("legacy message was confirmed while editor did not settle")
        return MessageConfirmationEvidence("dom", "legacy_baseline_count")
    if not isinstance(probe, MessageConfirmationProbe):
        raise TypeError("a pre-submit MessageConfirmationProbe is required")
    if _normalize_confirmation_text(probe.message) != _normalize_confirmation_text(message):
        raise ValueError("confirmation probe does not match the submitted message")

    signals: list[object] = []
    if probe.dom_available:
        signals.append(_wait_for_dom_confirmation(page, probe, timeout))
    if probe.network_monitor is not None and probe.network_monitor.available:
        signals.append(probe.network_monitor.wait())

    try:
        evidence = await _wait_for_first_confirmation(signals, timeout)
    finally:
        await dispose_message_confirmation_probe(page, probe)

    # Do not turn an already-observed outgoing bubble into a failure merely
    # because Douyin retained or replaced the editor DOM. This short wait is
    # useful for diagnostics and settling only.
    try:
        input_handle = await chat_input.element_handle()
        if input_handle is not None:
            await page.wait_for_function(
                "(element) => !String(element.innerText || element.textContent || '').trim()",
                arg=input_handle,
                timeout=min(timeout, 1500),
            )
    except Exception:
        logger.debug("message was confirmed while the chat editor did not settle")
    return evidence


async def retry_operation(name, operation, retries=3, delay=2, *args, **kwargs):
    """
    通用的重试逻辑
    :param name: 操作名称（用于日志记录）
    :param operation: 要执行的异步操作
    :param retries: 最大重试次数
    :param delay: 每次重试之间的延迟（秒）
    :param args: 传递给操作的参数
    :param kwargs: 传递给操作的关键字参数
    """
    for attempt in range(retries):
        try:
            return await operation(*args, **kwargs)
        except Exception as e:
            if attempt < retries - 1:
                logger.warning(f"{name} 失败，正在重试第 {attempt + 1} 次，错误：{e}")
                await asyncio.sleep(delay)
            else:
                logger.error(f"{name} 失败，已达到最大重试次数，错误：{e}")
                raise


async def scroll_and_select_user(page, username, targets):
    """尝试滚动并查找用户名"""
    # 定义目标元素和滚动容器的选择器
    friends_tab_selectors = [
        'xpath=//*[@id="sub-app"]/div/div/div[1]/div[2]',
        'xpath=//*[@id="sub-app"]//*[contains(@class, "tab") and contains(normalize-space(.), "好友")]',
        'xpath=//*[@id="sub-app"]//*[self::div or self::span][normalize-space()="好友"]',
    ]
    target_selector = 'xpath=//*[@id="sub-app"]//div[contains(@class, "semi-list-item-body") and contains(@class, "semi-list-item-body-flex-start")]'
    scrollable_friends_selector = 'xpath=//*[@id="sub-app"]//ul/ancestor::div[contains(@class, "semi-scrolllist") or contains(@style, "overflow")][1]'
    first_friend_selectors = [
        'xpath=//*[@id="sub-app"]/div/div/div[2]/div[2]/div/div/div[1]/div/div/div/ul/div/div/div[1]/li/div',
        'xpath=//*[@id="sub-app"]//span[contains(@class, "item-header-name-")]/ancestor::li[1]//div[1]',
        'xpath=//*[@id="sub-app"]//ul//li[1]//div',
        'css=#sub-app ul li:first-child div',
    ]
    
    # [修改] 更加精确的状态选择器
    no_more_selector = 'xpath=//div[contains(@class, "no-more-tip-ftdJnu")]'
    loading_selector = 'xpath=//div[contains(@class, "semi-spin")]'

    logger.debug(f"账号 {username} 开始查找目标好友列表")
    logger.debug(f"账号 {username} 目标好友列表: {targets}")

    logger.debug(f"账号 {username} 点击进入好友标签页")
    # 点击好友标签页
    if await page_has_login_prompt(page):
        raise RuntimeError(f"账号 {username} 登录已失效，需要重新扫码登录")
    try:
        friends_tab_selector = await wait_for_authenticated_selector(
            page, friends_tab_selectors
        )
    except LoginRequiredError as error:
        os.makedirs(os.path.join("logs", "diagnostics"), exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        await page.screenshot(
            path=os.path.join("logs", "diagnostics", f"login_expired_{timestamp}.png"),
            full_page=True,
        )
        raise RuntimeError(
            f"账号 {username} 登录已失效，需要重新扫码登录"
        ) from error
    except Exception as error:
        os.makedirs(os.path.join("logs", "diagnostics"), exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        await page.screenshot(
            path=os.path.join("logs", "diagnostics", f"friends_tab_{timestamp}.png"),
            full_page=True,
        )
        raise RuntimeError(
            f"账号 {username} 未找到好友标签，页面结构可能已变化"
        ) from error
    await page.locator(friends_tab_selector).click()

    logger.debug(f"账号 {username} 进入好友列表页面")

    # 确保第一个好友元素加载完成
    try:
        first_friend_selector = await wait_for_authenticated_selector(
            page, first_friend_selectors
        )
    except LoginRequiredError as error:
        raise RuntimeError(
            f"账号 {username} 登录已失效，需要重新扫码登录"
        ) from error
    except Exception as error:
        os.makedirs(os.path.join("logs", "diagnostics"), exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        await page.screenshot(
            path=os.path.join("logs", "diagnostics", f"friend_list_{timestamp}.png"),
            full_page=True,
        )
        raise RuntimeError(
            f"账号 {username} 未找到好友列表，页面结构可能已变化"
        ) from error
    await click_first_match(page, first_friend_selector)

    logger.debug(f"账号 {username} 已激活好友列表，开始滚动查找目标好友")

    await asyncio.sleep(2)  # 等待好友列表加载

    found_usernames = set()
    # [修改] 复制一份目标列表用于追踪进度
    remaining_targets = set(targets)

    while True:
        # 查找所有目标元素
        target_elements = await page.locator(target_selector).all()

        for element in target_elements:
            try:
                # 查找子元素 span，模糊匹配 class
                span = element.locator(
                    """xpath=.//span[contains(@class, "item-header-name-")]"""
                )
                targetName = await span.inner_text()

                if targetName in found_usernames:
                    continue  # 已处理过，跳过
                found_usernames.add(targetName)

                logger.debug(f"账号 {username} 找到好友 {targetName}")
                # 检查是否是目标用户名
                if targetName in targets:
                    await element.click()
                    logger.info(
                        f"账号 {username} 选中目标好友 {targetName} 准备开始交互"
                    )
                    yield targetName
                    
                    # [修改] 标记已找到，如果全找到了直接退出
                    if targetName in remaining_targets:
                        remaining_targets.remove(targetName)
                    if len(remaining_targets) == 0:
                        logger.info(f"账号 {username} 所有目标好友均已找到，停止搜索")
                        return
                    break
            except Exception as e:
                traceback.print_exc()
        else:
            # [修改] 状态检测逻辑
            
            # 1. 检查是否到底（没有更多了）
            if await page.locator(no_more_selector).count() > 0:
                logger.info(f"账号 {username} 检测到'没有更多了'标志，已到达底部")
                if len(remaining_targets) > 0:
                    logger.warning(f"账号 {username} 搜索结束，仍有以下好友未找到: {remaining_targets}")
                break

            # 2. 检查是否正在加载
            if await page.locator(loading_selector).count() > 0:
                logger.debug(f"账号 {username} 列表正在加载中 (Loading)...")
                await asyncio.sleep(1.5) # 给加载留点时间
                # 不 break，继续去滚动以触发后续内容

            # 3. 滚动容器
            scrollable_element = await page.locator(
                scrollable_friends_selector
            ).element_handle()
            
            if scrollable_element:
                # [修改] 加大滚动幅度
                await page.evaluate(
                    "(element) => element.scrollTop += 800", scrollable_element
                )
                logger.debug(f"账号 {username} 滚动好友列表以加载更多好友")
                await asyncio.sleep(1.5)
            else:
                logger.error(f"账号 {username} 未找到滚动容器，退出")
                break


async def do_user_task(browser, username, cookies, targets, semaphore):
    async with semaphore:  # 使用信号量控制并发数量
        context = await browser.new_context()  # 每个任务使用独立的上下文
        context.set_default_navigation_timeout(120000)  # 设置导航超时时间为 90 秒
        context.set_default_timeout(120000)  # 设置所有操作的默认超时时间为 120 秒

        page = await context.new_page()
        # 打开抖音创作者中心
        await retry_operation(
            "打开抖音创作者中心",
            page.goto,
            retries=3,
            delay=5,
            url="https://creator.douyin.com/",
        )
        # 注入 Cookie
        await context.add_cookies(cookies)

        # 导航到消息页面
        await retry_operation(
            "导航到消息页面",
            page.goto,
            retries=3,
            delay=5,
            url="https://creator.douyin.com/creator-micro/data/following/chat",
        )

        logger.info(f"账号 {username} 开始发送消息")
        # 滚动并选择用户
        async for username in scroll_and_select_user(page, username, targets):
            logger.info(f"账号 {username} 已选中好友 {username} 发送消息")
            # 等待聊天输入框元素加载完成，使用更稳定的属性选择器
            chat_input_selector = "xpath=//div[contains(@class, 'chat-input-')]"
            await page.wait_for_selector(chat_input_selector, timeout=30000)
            chat_input = page.locator(chat_input_selector)

            # 在 chat-input-dccKiL 中输入内容
            message = build_message()
            for line in message.split("\n"):
                await chat_input.type(line)  # 输入每一行
                # 如果不是最后一行，模拟 Shift+Enter 插入换行
                if line != message.split("\n")[-1]:
                    await chat_input.press("Shift+Enter")  # 模拟 Shift+Enter 插入换行

            logger.debug(
                f"账号 {username} 准备发送消息给好友 {username}：\n\t{message}"
            )
            # 模拟按下回车键发送消息
            confirmation_baseline = await capture_message_confirmation_baseline(
                page, message
            )
            await chat_input.press("Enter")
            try:
                await confirm_message_sent(
                    page,
                    chat_input,
                    message,
                    baseline_count=confirmation_baseline,
                )
            except Exception as error:
                os.makedirs(os.path.join("logs", "diagnostics"), exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                await page.screenshot(
                    path=os.path.join(
                        "logs", "diagnostics", f"message_unconfirmed_{timestamp}.png"
                    ),
                    full_page=True,
                )
                raise RuntimeError("消息已提交，但未能确认出现在聊天记录中") from error
            logger.info(f"账号 {username} 给好友 {username} 发送消息完成")
            await asyncio.sleep(2)  # 发送完等待一会儿

        await context.close()  # 任务完成后关闭上下文


async def runTasks():
    config = get_config()
    user_data = get_userData()
    playwright, browser = await get_browser()
    try:
        # 检查是否启用多任务和任务数量
        # 创建信号量以限制并发任务数量
        logger.info("开始执行任务,当前配置如下：")
        logger.info(f"多任务模式: {config['multiTask']}, 任务数量: {config['taskCount']}")
        logger.info(f"消息模板: {config['messageTemplate']}")
        logger.info(f"一言类型: {config['hitokotoTypes']}")
        for user in user_data:
            logger.info(f"用户: {user.get('username', '未知用户')}, 目标好友: {user['targets']}")
            
        semaphore = asyncio.Semaphore(config["taskCount"] if config["multiTask"] else 1)

        tasks = []
        for user in user_data:
            cookies = user["cookies"]
            targets = user["targets"]
            complates[user["unique_id"]] = []  # 初始化该用户的已完成列表
            username = user.get("username", "未知用户")
            # 创建任务
            tasks.append(do_user_task(browser, username, cookies, targets, semaphore))

        # 并发执行任务
        await asyncio.gather(*tasks)
    finally:
        await playwright.stop()

        # 关闭浏览器实例
        await browser.close()
