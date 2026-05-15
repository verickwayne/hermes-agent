import importlib.util
from pathlib import Path


def _load_capture_proxy_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "capture_anthropic_proxy.py"
    )
    spec = importlib.util.spec_from_file_location("capture_anthropic_proxy", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_build_capture_record_extracts_first_system_block():
    mod = _load_capture_proxy_module()
    record = mod._build_capture_record(
        request_url="https://api.anthropic.com/v1/messages",
        headers={"authorization": "Bearer test-token"},
        body=(
            b'{"system":[{"type":"text","text":"x-anthropic-billing-header: cc_version=2.1.87.abc;"}],'
            b'"messages":[{"role":"user","content":"hello"}]}'
        ),
    )

    assert record["request_url"] == "https://api.anthropic.com/v1/messages"
    assert record["headers"]["authorization"] == "Bearer test-token"
    assert record["first_system_block"] == "x-anthropic-billing-header: cc_version=2.1.87.abc;"


def test_build_capture_record_handles_non_json_body():
    mod = _load_capture_proxy_module()
    record = mod._build_capture_record(
        request_url="https://api.anthropic.com/v1/messages",
        headers={},
        body=b"not-json",
    )

    assert record["first_system_block"] is None


def test_split_connect_target_defaults_to_443():
    mod = _load_capture_proxy_module()

    assert mod._split_connect_target("api.anthropic.com") == ("api.anthropic.com", 443)
    assert mod._split_connect_target("api.anthropic.com:443") == ("api.anthropic.com", 443)


def test_build_proxy_env_contains_proxy_and_ca_bundle():
    mod = _load_capture_proxy_module()

    env = mod._build_proxy_env("http://127.0.0.1:8787", "/tmp/hermes-anthropic-mitm/ca.pem")

    assert env["HTTPS_PROXY"] == "http://127.0.0.1:8787"
    assert env["https_proxy"] == "http://127.0.0.1:8787"
    assert env["SSL_CERT_FILE"] == "/tmp/hermes-anthropic-mitm/ca.pem"
    assert env["NODE_EXTRA_CA_CERTS"] == "/tmp/hermes-anthropic-mitm/ca.pem"
