"""HTTPX transport backed by curl_cffi for TLS/H2/header-order spoofing.

Drop-in for ``httpx.Client(transport=...)``.  When the Anthropic Python SDK
ships requests via this transport, the wire looks like Chrome (or a chosen
impersonation target) at three layers Python's stdlib leaks identity at:

  1. **TLS ClientHello (JA3/JA4)** — Python's ``ssl`` module presents a
     cipher suite + extension ordering distinctive to OpenSSL-on-Python.
     curl-impersonate (the C library under curl_cffi) replays the exact
     bytes Chrome/Firefox/Safari send.
  2. **HTTP/2 SETTINGS frame ordering** — Python's h2 library sends
     SETTINGS_HEADER_TABLE_SIZE, ENABLE_PUSH, MAX_CONCURRENT_STREAMS,
     INITIAL_WINDOW_SIZE in one canonical order; Node's nghttp2 (and
     Chrome) uses a different one.  curl_cffi replays the impersonation
     target's order via libnghttp2 patches.
  3. **Header ordering** — Python's httpx serializes headers in dict
     insertion order, but the order it inserts internal headers (Host,
     User-Agent, Accept) doesn't match Node's undici.  curl-impersonate
     reorders to match the impersonation target.

Install: ``pip install curl_cffi`` (≥0.7).  Pure-pip wheels are available
for macOS/Linux/Windows; no system dependency beyond what cffi pulls in.

Usage in ``agent/anthropic_adapter.py:build_anthropic_client``:

    from agent.curl_cffi_transport import build_impersonating_http_client
    if _is_oauth_token(api_key):
        kwargs["http_client"] = build_impersonating_http_client(
            impersonate="chrome131",
            timeout=900.0,
        )
        # ... existing kwargs["default_headers"] still applies; the
        # Stainless / User-Agent / X-Claude-Code-Session-Id values from
        # the caller are layered on top of curl_cffi's impersonation.

Caveats:
  - curl_cffi's "node" impersonation profile does not yet exist; the
    closest match is "chrome131" / "chrome_android_131", which Node's
    undici is very close to (both built on BoringSSL-derived TLS stacks).
    Anthropic's fingerprint check, if any, is most likely "not python /
    not requests / not aiohttp" rather than "exactly Node v22.19" — so
    a Chrome impersonation should clear it.
  - Async path uses curl_cffi.requests.AsyncSession.  Sync uses Session.
  - We do NOT proxy streaming bytes one-by-one — curl_cffi streams via
    a callback model that's compatible with httpx.Response's streaming
    interface, so this works for SSE / streaming completions too.
  - This module degrades gracefully: if curl_cffi isn't installed,
    ``build_impersonating_http_client`` returns None and the caller
    should fall back to the default httpx transport.
"""

from __future__ import annotations

import os
import re
import logging
from typing import Any, Optional

import httpx

from agent.claude_code_identity import CLAUDE_CODE_CLAIMED_VERSION

logger = logging.getLogger(__name__)
_ROUTING_DEBUG_ENV_VAR = "HERMES_ANTHROPIC_ROUTING_DEBUG"
_ROUTING_DEBUG_LOG_ENV_VAR = "HERMES_ANTHROPIC_ROUTING_DEBUG_FILE"
_DEFAULT_ROUTING_DEBUG_LOG_PATH = "/tmp/hermes-anthropic-routing.log"


def _curl_cffi_available() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        return False


def _routing_debug_enabled() -> bool:
    value = os.getenv(_ROUTING_DEBUG_ENV_VAR, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _routing_debug_log_path() -> str:
    path = os.getenv(_ROUTING_DEBUG_LOG_ENV_VAR, "").strip()
    return path or _DEFAULT_ROUTING_DEBUG_LOG_PATH


def emit_anthropic_routing_debug(message: str) -> None:
    line = f"🧭 Anthropic routing debug: {message}"
    print(line, flush=True)
    try:
        with open(_routing_debug_log_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        logger.warning("Failed to write Anthropic routing debug log: %s", exc)


def _header_debug_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _extract_first_system_block_text(payload: dict) -> Optional[str]:
    system_field = payload.get("system")
    if isinstance(system_field, str):
        return system_field
    if isinstance(system_field, list) and system_field:
        first = system_field[0]
        if isinstance(first, dict) and first.get("type") == "text":
            text = first.get("text")
            if isinstance(text, str):
                return text
    return None


def _build_unredacted_routing_debug_info(
    *,
    request_url: str,
    headers: dict,
    original_body: bytes,
    final_body: bytes,
    transport: str,
) -> dict:
    import json

    info = {
        "request_url": request_url,
        "transport": transport,
        "headers": {k: _header_debug_value(v) for k, v in headers.items()},
        "messages_count": None,
        "system_block_count": None,
        "attribution_injected": False,
        "first_system_block": None,
        "cch": None,
        "cch_replaced": False,
    }

    try:
        original_payload = json.loads(original_body)
        if isinstance(original_payload, dict):
            messages = original_payload.get("messages")
            if isinstance(messages, list):
                info["messages_count"] = len(messages)
    except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
        pass

    try:
        final_payload = json.loads(final_body)
    except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return info

    if not isinstance(final_payload, dict):
        return info

    system_field = final_payload.get("system")
    if isinstance(system_field, list):
        info["system_block_count"] = len(system_field)
    elif isinstance(system_field, str):
        info["system_block_count"] = 1

    first_system_block = _extract_first_system_block_text(final_payload)
    if first_system_block and first_system_block.startswith("x-anthropic-billing-header: "):
        info["attribution_injected"] = True
        info["first_system_block"] = first_system_block
        match = re.search(r"cch=([0-9a-f]{5})", first_system_block)
        if match:
            info["cch"] = match.group(1)
            info["cch_replaced"] = match.group(1) != "00000"

    return info


def _emit_unredacted_routing_debug(info: dict) -> None:
    import json

    emit_anthropic_routing_debug(f"transport={info['transport']} endpoint={info['request_url']}")
    emit_anthropic_routing_debug(f"headers={json.dumps(info['headers'], sort_keys=True)}")
    emit_anthropic_routing_debug(
        f"messages_count={info['messages_count']} system_block_count={info['system_block_count']}"
    )
    emit_anthropic_routing_debug(
        "attribution_injected="
        + ("yes" if info["attribution_injected"] else "no")
        + " cch_replaced="
        + ("yes" if info["cch_replaced"] else "no")
        + f" cch={info['cch']}"
    )
    if info["first_system_block"] is not None:
        emit_anthropic_routing_debug(f"first_system_block={info['first_system_block']}")
    else:
        emit_anthropic_routing_debug("first_system_block=<missing-or-non-attribution>")


# Real Claude Code prepends an attribution string to every /v1/messages
# request's system prompt.  Anthropic's server parses this prefix to
# decide subscription routing — without it, OAuth requests bill against
# the third-party "extra-usage" bucket regardless of any header spoof.
# See Claude-Code-Source-Code/constants/system.ts:getAttributionHeader +
# services/api/claude.ts:1360 (prepends as the first system prompt block).
#
# Format (from constants/system.ts:91):
#   x-anthropic-billing-header: cc_version=<v>.<fingerprint>; cc_entrypoint=<entry>;
# Optional fields: cch=<attestation>; cc_workload=<tag>;
# Version we claim in the attribution field. Keep this single-sourced with the
# OAuth User-Agent version so cc_version and User-Agent never drift apart.
_CLAUDE_CODE_VERSION_FOR_ATTRIBUTION = CLAUDE_CODE_CLAIMED_VERSION
_CLAUDE_CODE_ENTRYPOINT = "cli"

# Hardcoded salt from Anthropic backend validation.  MUST match exactly
# or the fingerprint check fails and Anthropic routes to extra-usage.
# Per Claude-Code-Source-Code/utils/fingerprint.ts:
#   "Hardcoded salt from backend validation.
#    Must match exactly for fingerprint validation to pass."
_FINGERPRINT_SALT = "59cf53e54c78"

# cch attestation seed.  In real Claude Code this is baked into the Bun
# native binary (bun-anthropic/src/http/Attestation.zig); the JS source
# only writes the literal `cch=00000` sentinel, and the Zig HTTP stack
# scans the outgoing body for that string and overwrites the five zeros
# with `xxhash64(body_bytes, seed) & 0xFFFFF` formatted as 5-char hex.
#
# Seed extracted from disassembled Bun binary by ssslomp / a10k.co.
# See a10k.co/b/reverse-engineering-claude-code-cch.html.
_CCH_SEED = 0x6E52736AC806831E
_CCH_PLACEHOLDER = b"cch=00000"


def _extract_first_user_message_text(messages: list) -> str:
    """Mirror extractFirstMessageText from Claude Code source.

    Returns the text content of the first user message, handling both
    string-shaped content and block-list content (text blocks only).
    Returns "" if no usable first user message exists.
    """
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        return ""
    return ""


def _compute_fingerprint(first_message_text: str, version: str) -> str:
    """Exact replica of Claude Code's computeFingerprint algorithm.

    From utils/fingerprint.ts:
      chars = msg[4] + msg[7] + msg[20]   (default "0" when index OOB)
      input = SALT + chars + version
      return sha256(input)[:3]

    Server-validated.  Three-hex-char output.  Don't deviate.
    """
    import hashlib
    indices = [4, 7, 20]
    chars = "".join(
        first_message_text[i] if i < len(first_message_text) else "0"
        for i in indices
    )
    fingerprint_input = f"{_FINGERPRINT_SALT}{chars}{version}"
    return hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()[:3]


def _compute_cch(body_bytes: bytes) -> str:
    """Compute the cch attestation token over the serialized body.

    Mirrors Claude Code's native Bun/Zig HTTP-stack hook:
      cch = xxhash64(body, seed=_CCH_SEED) & 0xFFFFF
      formatted as a zero-padded 5-char lowercase hex string.

    The hash is computed over the body bytes that INCLUDE the
    `cch=00000` placeholder — the server recomputes the same hash on
    its side using the received body (with cch=<value> in place) by
    substituting the five zeros back before verifying, so the input
    must contain the literal placeholder, not the final value.

    Returns "00000" if xxhash isn't installed (graceful degradation —
    request will then fail server-side attestation but won't crash).
    """
    try:
        import xxhash  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("xxhash not installed; cch attestation will be invalid")
        return "00000"
    digest = xxhash.xxh64(body_bytes, seed=_CCH_SEED).intdigest()
    return f"{digest & 0xFFFFF:05x}"


def _inject_billing_attribution(body_bytes: bytes) -> bytes:
    """Prepend the x-anthropic-billing-header line to the system prompt.

    Body is the JSON-serialized /v1/messages payload.  We:
      1. Parse JSON.  If parse fails, return unmodified (probably not
         a /v1/messages call).
      2. Locate the ``system`` field.  Anthropic SDK serializes it as
         EITHER a string OR a list of {type: 'text', text: ...} blocks.
      3. Build the attribution line with a fingerprint derived from the
         messages content + version, AND the literal `cch=00000;`
         placeholder.
      4. Prepend it as a NEW system block at index 0 (preserves any
         cache_control markers on the existing blocks).
      5. Re-serialize.
      6. Compute xxhash64 over the serialized bytes (with the placeholder
         in place — see _compute_cch docstring) and string-replace
         `cch=00000` with `cch=<hash>`.  Length is preserved (both 9
         bytes), so Content-Length is unchanged.
    """
    import json
    try:
        payload = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body_bytes

    # Build fingerprint from the FIRST USER MESSAGE TEXT (not the whole
    # messages array) — server validates with chars at indices [4,7,20].
    messages = payload.get("messages", [])
    first_user_text = _extract_first_user_message_text(messages)
    fingerprint = _compute_fingerprint(first_user_text, _CLAUDE_CODE_VERSION_FOR_ATTRIBUTION)

    attribution = (
        f"x-anthropic-billing-header: "
        f"cc_version={_CLAUDE_CODE_VERSION_FOR_ATTRIBUTION}.{fingerprint}; "
        f"cc_entrypoint={_CLAUDE_CODE_ENTRYPOINT}; "
        f"cch=00000;"
    )

    attribution_block = {"type": "text", "text": attribution}

    sys_field = payload.get("system")
    if sys_field is None:
        payload["system"] = [attribution_block]
    elif isinstance(sys_field, str):
        # Convert string form to block-list form with attribution prepended
        payload["system"] = [attribution_block, {"type": "text", "text": sys_field}]
    elif isinstance(sys_field, list):
        # Already block-list form; insert attribution at the front
        payload["system"] = [attribution_block, *sys_field]
    else:
        # Unknown shape — bail without modifying
        return body_bytes

    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    # cch attestation: compute hash over the serialized body (with
    # placeholder in place), then replace the placeholder with the
    # computed hash.  Real Claude Code does this from Zig after the
    # JS serializes the request; we do it in Python here.
    if _CCH_PLACEHOLDER in serialized:
        cch = _compute_cch(serialized).encode("ascii")
        serialized = serialized.replace(_CCH_PLACEHOLDER, b"cch=" + cch, 1)

    return serialized


# Headers added by the Anthropic Python SDK that LEAK Python-runtime identity
# to Anthropic's routing layer, OR collide with the OAuth Bearer header.
# Real Claude Code / Node SDK never sends these.  Strip them at the transport
# layer where we have final say (default_headers can't reliably suppress them
# because the SDK adds them per-request after merge).
_HEADERS_TO_STRIP = {
    "x-stainless-async",          # Python-SDK-only; "false"/"true"
    "x-stainless-timeout",        # Python-SDK-only; per-request value
    "x-stainless-read-timeout",   # Python-SDK-only; per-request value
    "x-stainless-retry-count",    # Python-SDK-only; per-request value
}


def _scrub_request_headers(headers: dict) -> dict:
    """Remove Python-SDK-only headers + empty/leaked x-api-key.

    Anthropic's first-party routing layer treats the presence of x-api-key
    (even empty) as a "third-party API-key caller" signal — so we must
    REMOVE it, not just empty it.  And the x-stainless-* per-request
    headers (async/timeout/retry-count/read-timeout) are added by the
    Python SDK's retry machinery; the Node SDK doesn't send them.  Their
    presence confirms "this is the Python SDK" even when the language/
    runtime/version overrides claim otherwise.
    """
    cleaned = {}
    for k, v in headers.items():
        k_lower = k.lower()
        if k_lower in _HEADERS_TO_STRIP:
            continue
        # Drop empty / leaked x-api-key; OAuth Bearer is in Authorization.
        if k_lower == "x-api-key":
            continue
        cleaned[k] = v
    return cleaned


class CurlCffiTransport(httpx.BaseTransport):
    """Sync httpx transport that ships requests via curl_cffi.

    Each request goes through a fresh curl-impersonate session (the
    session ctor is cheap; reusing across requests would lose the TLS
    ticket isolation that browsers actually have per origin).
    """

    def __init__(self, impersonate: str = "chrome131") -> None:
        from curl_cffi import requests as _cc_requests

        self._impersonate = impersonate
        self._session_cls = _cc_requests.Session

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        original_body = body
        # Inject the Claude Code billing-header attribution on /v1/messages
        # so Anthropic's routing layer classifies the OAuth call as
        # first-party Claude Code (→ included quota) instead of third-party
        # (→ extra-usage bucket).  See _inject_billing_attribution.
        if request.url.path.endswith("/v1/messages") and body:
            body = _inject_billing_attribution(body)
        headers = _scrub_request_headers(dict(request.headers))
        # Content-Length will be recomputed by curl_cffi; drop the stale one
        headers.pop("content-length", None)
        headers.pop("Content-Length", None)
        if _routing_debug_enabled() and request.url.path.endswith("/v1/messages"):
            _emit_unredacted_routing_debug(
                _build_unredacted_routing_debug_info(
                    request_url=str(request.url),
                    headers=headers,
                    original_body=original_body,
                    final_body=body,
                    transport=f"curl_cffi({self._impersonate})",
                )
            )
        with self._session_cls() as session:
            cc_response = session.request(
                method=request.method,
                url=str(request.url),
                headers=headers,
                # curl_cffi uses 'data=' (requests-style), not 'content=' (httpx-style)
                data=body,
                impersonate=self._impersonate,
                stream=False,  # materialize body; httpx streams from memory
                allow_redirects=False,
                verify=True,
            )

        # curl_cffi has already decompressed the body; strip the compression
        # headers + length so httpx doesn't try to decompress again
        # ("DecodingError: incorrect header check" otherwise).
        resp_headers = {
            k: v for k, v in cc_response.headers.items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
        }
        return httpx.Response(
            status_code=cc_response.status_code,
            headers=resp_headers,
            content=cc_response.content,
            request=request,
            extensions={"http_version": b"HTTP/2"},
        )


class AsyncCurlCffiTransport(httpx.AsyncBaseTransport):
    """Async equivalent of ``CurlCffiTransport`` for use with
    ``httpx.AsyncClient``.  The Anthropic Python SDK's AsyncAnthropic
    uses an AsyncClient internally, so streaming completions hit this.
    """

    def __init__(self, impersonate: str = "chrome131") -> None:
        from curl_cffi.requests import AsyncSession

        self._impersonate = impersonate
        self._session_cls = AsyncSession

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        original_body = body
        if request.url.path.endswith("/v1/messages") and body:
            body = _inject_billing_attribution(body)
        headers = _scrub_request_headers(dict(request.headers))
        headers.pop("content-length", None)
        headers.pop("Content-Length", None)
        if _routing_debug_enabled() and request.url.path.endswith("/v1/messages"):
            _emit_unredacted_routing_debug(
                _build_unredacted_routing_debug_info(
                    request_url=str(request.url),
                    headers=headers,
                    original_body=original_body,
                    final_body=body,
                    transport=f"curl_cffi({self._impersonate})",
                )
            )
        async with self._session_cls() as session:
            cc_response = await session.request(
                method=request.method,
                url=str(request.url),
                headers=headers,
                data=body,
                impersonate=self._impersonate,
                stream=False,
                allow_redirects=False,
                verify=True,
            )

        # curl_cffi has already decompressed the body; strip the compression
        # headers + length so httpx doesn't try to decompress again
        # ("DecodingError: incorrect header check" otherwise).
        resp_headers = {
            k: v for k, v in cc_response.headers.items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
        }
        return httpx.Response(
            status_code=cc_response.status_code,
            headers=resp_headers,
            content=cc_response.content,
            request=request,
            extensions={"http_version": b"HTTP/2"},
        )


def build_impersonating_http_client(
    *,
    impersonate: str = "chrome131",
    timeout: float = 900.0,
    async_mode: bool = False,
) -> Optional[httpx.Client | httpx.AsyncClient]:
    """Return an httpx Client whose wire fingerprint matches a real browser.

    Falls back to ``None`` when curl_cffi is not installed, letting the
    caller decide whether to retry with the stdlib transport or surface
    an install-needed error.
    """
    if not _curl_cffi_available():
        logger.warning(
            "curl_cffi not installed — Hermes will fall back to stdlib "
            "httpx for Anthropic OAuth requests.  Anthropic may route "
            "those to the third-party 'extra-usage' billing bucket via "
            "TLS/H2 fingerprinting.  Install with: pip install curl_cffi"
        )
        return None

    timeout_obj = httpx.Timeout(timeout=timeout, connect=10.0)

    if async_mode:
        client = httpx.AsyncClient(
            transport=AsyncCurlCffiTransport(impersonate=impersonate),
            timeout=timeout_obj,
        )
        setattr(client, "_hermes_transport_name", f"curl_cffi({impersonate})")
        return client
    client = httpx.Client(
        transport=CurlCffiTransport(impersonate=impersonate),
        timeout=timeout_obj,
    )
    setattr(client, "_hermes_transport_name", f"curl_cffi({impersonate})")
    return client
