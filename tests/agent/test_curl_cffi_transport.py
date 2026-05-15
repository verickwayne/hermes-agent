import json
from types import SimpleNamespace

from agent.curl_cffi_transport import (
    CurlCffiTransport,
    _build_unredacted_routing_debug_info,
    _inject_billing_attribution,
    _routing_debug_log_path,
    emit_anthropic_routing_debug,
)
import httpx


def test_routing_debug_info_reports_injected_attribution_and_cch():
    original_body = json.dumps(
        {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hello from hermes"}],
            "system": [{"type": "text", "text": "existing system block"}],
        },
        separators=(",", ":"),
    ).encode("utf-8")

    final_body = _inject_billing_attribution(original_body)
    info = _build_unredacted_routing_debug_info(
        request_url="https://api.anthropic.com/v1/messages",
        headers={
            "Authorization": "Bearer test-token",
            "User-Agent": "claude-cli/test (user, cli)",
        },
        original_body=original_body,
        final_body=final_body,
        transport="curl_cffi(chrome131)",
    )

    assert info["request_url"] == "https://api.anthropic.com/v1/messages"
    assert info["transport"] == "curl_cffi(chrome131)"
    assert info["headers"]["Authorization"] == "Bearer test-token"
    assert info["messages_count"] == 1
    assert info["system_block_count"] == 2
    assert info["attribution_injected"] is True
    assert info["first_system_block"].startswith("x-anthropic-billing-header: ")
    assert "cc_version=2.1.142." in info["first_system_block"]
    assert info["cch"] is not None
    assert len(info["cch"]) == 5
    assert info["cch_replaced"] is True


def test_routing_debug_info_ignores_non_attribution_system_block():
    body = json.dumps(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "system": [{"type": "text", "text": "user-supplied system prompt"}],
        },
        separators=(",", ":"),
    ).encode("utf-8")

    info = _build_unredacted_routing_debug_info(
        request_url="https://api.anthropic.com/v1/messages",
        headers={},
        original_body=body,
        final_body=body,
        transport="curl_cffi(chrome131)",
    )

    assert info["attribution_injected"] is False
    assert info["first_system_block"] is None
    assert info["cch"] is None
    assert info["cch_replaced"] is False


def test_emit_anthropic_routing_debug_writes_default_or_overridden_log(tmp_path, monkeypatch, capsys):
    log_path = tmp_path / "routing.log"
    monkeypatch.setenv("HERMES_ANTHROPIC_ROUTING_DEBUG_FILE", str(log_path))

    emit_anthropic_routing_debug("transport=curl_cffi(chrome131)")

    captured = capsys.readouterr()
    assert "transport=curl_cffi(chrome131)" in captured.out
    assert _routing_debug_log_path() == str(log_path)
    assert "transport=curl_cffi(chrome131)" in log_path.read_text(encoding="utf-8")


def test_sync_transport_disables_curl_cffi_default_browser_headers():
    captured = {}

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status_code=200,
                headers={},
                content=b"{}",
            )

    transport = CurlCffiTransport()
    transport._session_cls = _FakeSession

    request = httpx.Request(
        "POST",
        "https://api.anthropic.com/v1/messages?beta=true",
        headers={"Authorization": "Bearer test-token"},
        content=b'{"messages":[{"role":"user","content":"hello"}]}',
    )
    response = transport.handle_request(request)

    assert response.status_code == 200
    assert captured["default_headers"] is False
    assert captured["accept_encoding"] == "gzip, deflate, br, zstd"
    assert captured["impersonate"] == "chrome131"
