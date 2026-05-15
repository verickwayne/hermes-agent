#!/usr/bin/env python3
"""Capture Claude Code Anthropic routing and forward traffic upstream.

Usage:
  python scripts/capture_anthropic_proxy.py \
    --listen-host 127.0.0.1 \
    --listen-port 8787 \
    --capture-file /tmp/claude-routing-capture.json \
    --append-log /tmp/claude-routing-capture.jsonl

Then launch Claude Code with:
  ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude

The proxy forwards requests to https://api.anthropic.com by default, and for
POST /v1/messages writes the latest unredacted capture JSON in the shape
consumed by scripts/compare_anthropic_routing.py.
"""

from __future__ import annotations

import argparse
import json
import ssl
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


class _CaptureProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_base_url = "https://api.anthropic.com"
    capture_file = Path("/tmp/claude-routing-capture.json")
    append_log: Path | None = None
    timeout_seconds = 300

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def do_PUT(self) -> None:
        self._forward()

    def do_PATCH(self) -> None:
        self._forward()

    def do_DELETE(self) -> None:
        self._forward()

    def _forward(self) -> None:
        body = self._read_request_body()
        upstream_url = f"{self.upstream_base_url.rstrip('/')}{self.path}"
        response = self._send_upstream(upstream_url, body)
        response_body = response.read()

        if self.command == "POST" and self.path == "/v1/messages":
            headers = {key.lower(): value for key, value in self.headers.items()}
            record = _build_capture_record(
                request_url=upstream_url,
                headers=headers,
                body=body,
            )
            self.capture_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
            if self.append_log is not None:
                with self.append_log.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record))
                    fh.write("\n")
            print(f"[capture] wrote {self.capture_file} from {self.client_address[0]}")

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

    def _read_request_body(self) -> bytes:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(content_length) if content_length else b""

    def _send_upstream(self, upstream_url: str, body: bytes) -> HTTPResponse:
        split = urlsplit(upstream_url)
        connection_cls = HTTPSConnection if split.scheme == "https" else HTTPConnection
        port = split.port or (443 if split.scheme == "https" else 80)
        context = ssl.create_default_context() if split.scheme == "https" else None
        conn = connection_cls(
            split.hostname,
            port,
            timeout=self.timeout_seconds,
            context=context,
        ) if split.scheme == "https" else connection_cls(
            split.hostname,
            port,
            timeout=self.timeout_seconds,
        )
        path = split.path or "/"
        if split.query:
            path = f"{path}?{split.query}"
        headers = dict(self.headers.items())
        headers["Host"] = split.netloc
        headers.pop("Proxy-Connection", None)
        conn.request(self.command, path, body=body, headers=headers)
        return conn.getresponse()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=8787)
    parser.add_argument("--upstream-base-url", default="https://api.anthropic.com")
    parser.add_argument("--capture-file", default="/tmp/claude-routing-capture.json")
    parser.add_argument("--append-log", default="/tmp/claude-routing-capture.jsonl")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _CaptureProxyHandler.upstream_base_url = args.upstream_base_url
    _CaptureProxyHandler.capture_file = Path(args.capture_file)
    _CaptureProxyHandler.append_log = Path(args.append_log) if args.append_log else None
    server = ThreadingHTTPServer((args.listen_host, args.listen_port), _CaptureProxyHandler)
    print(f"[capture] listening on http://{args.listen_host}:{args.listen_port}")
    print(f"[capture] forwarding to {args.upstream_base_url}")
    print(f"[capture] latest capture file: {args.capture_file}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
