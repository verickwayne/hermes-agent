#!/usr/bin/env python3
"""Capture Anthropic routing from Hermes or Claude Code.

Two modes are supported:

1. `--mode base-url`
   A plain HTTP forwarder for clients that let you override the Anthropic base
   URL directly.

2. `--mode https-proxy`
   A CONNECT MITM proxy for the installed `claude` binary. This is the path we
   need for real Claude Code capture because subscriber traffic honors
   `HTTPS_PROXY`, not `ANTHROPIC_BASE_URL`.

Example: real Claude Code capture
  python scripts/capture_anthropic_proxy.py --mode https-proxy
  HTTPS_PROXY=http://127.0.0.1:8787 \
  SSL_CERT_FILE=/tmp/hermes-anthropic-mitm/ca.pem \
  NODE_EXTRA_CA_CERTS=/tmp/hermes-anthropic-mitm/ca.pem \
  claude

Example: legacy base-url capture
  python scripts/capture_anthropic_proxy.py --mode base-url
  ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
"""

from __future__ import annotations

import argparse
import json
import select
import socket
import ssl
import subprocess
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def _load_json_body(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _extract_first_system_block(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    system = payload.get("system")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    return text
    return None


def _build_capture_record(*, request_url: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    payload = _load_json_body(body)
    return {
        "request_url": request_url,
        "headers": headers,
        "first_system_block": _extract_first_system_block(payload),
    }


def _split_connect_target(target: str) -> tuple[str, int]:
    host, _, port_s = target.rpartition(":")
    if not host:
        return target, 443
    try:
        return host, int(port_s)
    except ValueError:
        return target, 443


def _build_proxy_env(proxy_url: str, ca_cert_path: str) -> dict[str, str]:
    return {
        "HTTPS_PROXY": proxy_url,
        "https_proxy": proxy_url,
        "SSL_CERT_FILE": ca_cert_path,
        "NODE_EXTRA_CA_CERTS": ca_cert_path,
    }


def _write_text_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _run_openssl(args: list[str]) -> None:
    subprocess.run(
        ["openssl", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _ensure_mitm_material(ca_dir: Path, target_host: str) -> tuple[Path, Path]:
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_key = ca_dir / "ca.key"
    ca_cert = ca_dir / "ca.pem"
    leaf_key = ca_dir / f"{target_host}.key"
    leaf_csr = ca_dir / f"{target_host}.csr"
    leaf_cert = ca_dir / f"{target_host}.pem"
    san_cfg = ca_dir / f"{target_host}.cnf"

    if not ca_key.exists():
        _run_openssl(["genrsa", "-out", str(ca_key), "2048"])
    if not ca_cert.exists():
        _run_openssl([
            "req",
            "-x509",
            "-new",
            "-key",
            str(ca_key),
            "-sha256",
            "-days",
            "3650",
            "-subj",
            "/CN=Hermes Anthropic Capture CA",
            "-out",
            str(ca_cert),
        ])
    if not leaf_key.exists():
        _run_openssl(["genrsa", "-out", str(leaf_key), "2048"])

    _write_text_if_missing(
        san_cfg,
        "\n".join(
            [
                "[req]",
                "distinguished_name=req_distinguished_name",
                "req_extensions=v3_req",
                "prompt=no",
                "[req_distinguished_name]",
                f"CN={target_host}",
                "[v3_req]",
                "subjectAltName=@alt_names",
                "extendedKeyUsage=serverAuth",
                "keyUsage=digitalSignature,keyEncipherment",
                "[alt_names]",
                f"DNS.1={target_host}",
                "",
            ]
        ),
    )

    if not leaf_csr.exists():
        _run_openssl([
            "req",
            "-new",
            "-key",
            str(leaf_key),
            "-out",
            str(leaf_csr),
            "-config",
            str(san_cfg),
        ])
    if not leaf_cert.exists():
        _run_openssl([
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(ca_cert),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(leaf_cert),
            "-days",
            "825",
            "-sha256",
            "-extensions",
            "v3_req",
            "-extfile",
            str(san_cfg),
        ])

    return leaf_cert, leaf_key


class _CaptureProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_base_url = "https://api.anthropic.com"
    capture_file = Path("/tmp/claude-routing-capture.json")
    append_log: Path | None = None
    timeout_seconds = 300
    mode = "base-url"
    mitm_target_host = "api.anthropic.com"
    mitm_cert_file: Path | None = None
    mitm_key_file: Path | None = None

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_CONNECT(self) -> None:
        # CONNECT tunnels own the socket lifecycle; after we hand off to the
        # tunnel/MITM handler, BaseHTTPRequestHandler must not try to parse a
        # second request from the original socket wrapper.
        self.close_connection = True
        host, port = _split_connect_target(self.path)
        if (
            self.mode == "https-proxy"
            and host.lower() == self.mitm_target_host.lower()
            and port == 443
            and self.mitm_cert_file is not None
            and self.mitm_key_file is not None
        ):
            self._handle_mitm_connect(host, port)
            return
        self._handle_passthrough_connect(host, port)

    def do_GET(self) -> None:
        self._forward_plain_http()

    def do_POST(self) -> None:
        self._forward_plain_http()

    def do_PUT(self) -> None:
        self._forward_plain_http()

    def do_PATCH(self) -> None:
        self._forward_plain_http()

    def do_DELETE(self) -> None:
        self._forward_plain_http()

    def _forward_plain_http(self) -> None:
        body = self._read_request_body()
        upstream_url = f"{self.upstream_base_url.rstrip('/')}{self.path}"
        response = self._send_upstream(self.command, upstream_url, body, dict(self.headers.items()))
        response_body = response.read()

        if self.command == "POST" and self.path == "/v1/messages":
            self._persist_capture(
                request_url=upstream_url,
                headers={key.lower(): value for key, value in self.headers.items()},
                body=body,
            )

        self.send_response(response.status, response.reason)
        for key, value in response.getheaders():
            lower = key.lower()
            if lower in {"transfer-encoding", "connection", "content-length"}:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        if response_body:
            self.wfile.write(response_body)

    def _handle_mitm_connect(self, host: str, port: int) -> None:
        self.send_response(200, "Connection Established")
        self.end_headers()

        assert self.mitm_cert_file is not None
        assert self.mitm_key_file is not None

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            certfile=str(self.mitm_cert_file),
            keyfile=str(self.mitm_key_file),
        )
        tls_sock = context.wrap_socket(self.connection, server_side=True)
        tls_rfile = tls_sock.makefile("rb")

        try:
            while True:
                request_line = tls_rfile.readline(65536)
                if not request_line:
                    break
                if request_line in {b"\r\n", b"\n"}:
                    continue
                try:
                    method, path, _http_version = request_line.decode("iso-8859-1").strip().split(" ", 2)
                except ValueError:
                    break
                headers = self._read_headers_from_stream(tls_rfile)
                content_length = int(headers.get("Content-Length", "0") or "0")
                body = tls_rfile.read(content_length) if content_length else b""
                request_url = f"https://{host}{path}"
                response = self._send_upstream(method, request_url, body, headers)
                response_body = response.read()

                if method == "POST" and path.startswith("/v1/messages"):
                    self._persist_capture(
                        request_url=request_url,
                        headers={key.lower(): value for key, value in headers.items()},
                        body=body,
                    )

                self._write_tls_response(tls_sock, response, response_body)
                if headers.get("Connection", "").lower() == "close":
                    break
        finally:
            try:
                tls_rfile.close()
            except OSError:
                pass
            try:
                tls_sock.close()
            except OSError:
                pass

    def _handle_passthrough_connect(self, host: str, port: int) -> None:
        try:
            upstream = socket.create_connection((host, port), timeout=self.timeout_seconds)
        except OSError:
            self.send_error(502, "Bad Gateway")
            return

        self.send_response(200, "Connection Established")
        self.end_headers()

        sockets = [self.connection, upstream]
        try:
            while True:
                readable, _, _ = select.select(sockets, [], [], self.timeout_seconds)
                if not readable:
                    break
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    if sock is self.connection:
                        upstream.sendall(data)
                    else:
                        self.connection.sendall(data)
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    def _write_tls_response(
        self,
        sock: ssl.SSLSocket,
        response: HTTPResponse,
        response_body: bytes,
    ) -> None:
        status_line = f"HTTP/1.1 {response.status} {response.reason}\r\n".encode("iso-8859-1")
        sock.sendall(status_line)
        for key, value in response.getheaders():
            lower = key.lower()
            if lower in {"transfer-encoding", "connection", "content-length"}:
                continue
            header_line = f"{key}: {value}\r\n".encode("iso-8859-1")
            sock.sendall(header_line)
        sock.sendall(f"Content-Length: {len(response_body)}\r\n".encode("iso-8859-1"))
        sock.sendall(b"Connection: close\r\n\r\n")
        if response_body:
            sock.sendall(response_body)

    def _read_headers_from_stream(self, stream: Any) -> dict[str, str]:
        headers: dict[str, str] = {}
        while True:
            line = stream.readline(65536)
            if not line or line in {b"\r\n", b"\n"}:
                break
            decoded = line.decode("iso-8859-1")
            if ":" not in decoded:
                continue
            key, value = decoded.split(":", 1)
            headers[key.strip()] = value.strip()
        return headers

    def _persist_capture(self, *, request_url: str, headers: dict[str, str], body: bytes) -> None:
        record = _build_capture_record(
            request_url=request_url,
            headers=headers,
            body=body,
        )
        self.capture_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
        if self.append_log is not None:
            with self.append_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record))
                fh.write("\n")
        print(f"[capture] wrote {self.capture_file} from {self.client_address[0]}")

    def _read_request_body(self) -> bytes:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(content_length) if content_length else b""

    def _send_upstream(
        self,
        method: str,
        upstream_url: str,
        body: bytes,
        headers: dict[str, str],
    ) -> HTTPResponse:
        split = urlsplit(upstream_url)
        connection_cls = HTTPSConnection if split.scheme == "https" else HTTPConnection
        port = split.port or (443 if split.scheme == "https" else 80)
        context = ssl.create_default_context() if split.scheme == "https" else None
        conn = (
            connection_cls(split.hostname, port, timeout=self.timeout_seconds, context=context)
            if split.scheme == "https"
            else connection_cls(split.hostname, port, timeout=self.timeout_seconds)
        )
        path = split.path or "/"
        if split.query:
            path = f"{path}?{split.query}"
        headers = dict(headers)
        headers["Host"] = split.netloc
        headers.pop("Proxy-Connection", None)
        headers.pop("proxy-connection", None)
        conn.request(method, path, body=body, headers=headers)
        return conn.getresponse()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("base-url", "https-proxy"), default="https-proxy")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8787)
    parser.add_argument("--upstream-base-url", default="https://api.anthropic.com")
    parser.add_argument("--capture-file", default="/tmp/claude-routing-capture.json")
    parser.add_argument("--append-log", default="/tmp/claude-routing-capture.jsonl")
    parser.add_argument("--ca-dir", default="/tmp/hermes-anthropic-mitm")
    parser.add_argument("--mitm-target-host", default="api.anthropic.com")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _CaptureProxyHandler.mode = args.mode
    _CaptureProxyHandler.upstream_base_url = args.upstream_base_url
    _CaptureProxyHandler.capture_file = Path(args.capture_file)
    _CaptureProxyHandler.append_log = Path(args.append_log) if args.append_log else None
    _CaptureProxyHandler.mitm_target_host = args.mitm_target_host

    if args.mode == "https-proxy":
        cert_file, key_file = _ensure_mitm_material(Path(args.ca_dir), args.mitm_target_host)
        _CaptureProxyHandler.mitm_cert_file = cert_file
        _CaptureProxyHandler.mitm_key_file = key_file

    server = ThreadingHTTPServer((args.listen_host, args.listen_port), _CaptureProxyHandler)
    proxy_url = f"http://{args.listen_host}:{args.listen_port}"

    print(f"[capture] listening on {proxy_url}")
    print(f"[capture] mode: {args.mode}")
    print(f"[capture] forwarding to {args.upstream_base_url}")
    print(f"[capture] latest capture file: {args.capture_file}")
    if args.mode == "https-proxy":
        ca_cert = str(Path(args.ca_dir) / "ca.pem")
        env_vars = _build_proxy_env(proxy_url, ca_cert)
        print(f"[capture] MITM target: {args.mitm_target_host}:443")
        print(f"[capture] CA bundle: {ca_cert}")
        print("[capture] launch Claude Code with:")
        print(
            " ".join(
                [
                    f"{key}={value}"
                    for key, value in env_vars.items()
                ]
                + ["claude"]
            )
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
