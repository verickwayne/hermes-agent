import json

from agent.curl_cffi_transport import (
    _build_unredacted_routing_debug_info,
    _inject_billing_attribution,
    _routing_debug_log_path,
    emit_anthropic_routing_debug,
)


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
