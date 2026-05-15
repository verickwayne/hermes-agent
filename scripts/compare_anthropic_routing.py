#!/usr/bin/env python3
"""Compare Hermes Anthropic routing output against a Claude Code capture.

Expected inputs:
  1. Hermes routing log produced by HERMES_ANTHROPIC_ROUTING_DEBUG=1
  2. Claude Code capture JSON emitted by an external capture/proxy workflow

The Claude capture JSON shape this script expects is:
{
  "headers": {...},
  "first_system_block": "...",
  "request_url": "https://api.anthropic.com/v1/messages"
}
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _load_hermes_log(path: Path) -> dict:
    info: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "headers=" in line:
            payload = line.split("headers=", 1)[1]
            info["headers"] = json.loads(payload)
        elif "first_system_block=" in line:
            info["first_system_block"] = line.split("first_system_block=", 1)[1]
        elif "transport=" in line and "endpoint=" in line:
            match = re.search(r"transport=(.+?) endpoint=(.+)$", line)
            if match:
                info["transport"] = match.group(1)
                info["request_url"] = match.group(2)
    return info


def _load_claude_capture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _compare(hermes: dict, claude: dict) -> list[str]:
    diffs: list[str] = []
    for key in ("request_url",):
        if hermes.get(key) != claude.get(key):
            diffs.append(f"{key}: hermes={hermes.get(key)!r} claude={claude.get(key)!r}")

    hermes_headers = {str(k).lower(): str(v) for k, v in (hermes.get("headers") or {}).items()}
    claude_headers = {str(k).lower(): str(v) for k, v in (claude.get("headers") or {}).items()}
    header_keys = sorted(set(hermes_headers) | set(claude_headers))
    for key in header_keys:
        if hermes_headers.get(key) != claude_headers.get(key):
            diffs.append(
                f"header:{key}: hermes={hermes_headers.get(key)!r} claude={claude_headers.get(key)!r}"
            )

    if hermes.get("first_system_block") != claude.get("first_system_block"):
        diffs.append(
            "first_system_block: "
            f"hermes={hermes.get('first_system_block')!r} "
            f"claude={claude.get('first_system_block')!r}"
        )
    return diffs


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: compare_anthropic_routing.py HERMES_LOG CLAUDE_CAPTURE_JSON", file=sys.stderr)
        return 2

    hermes_path = Path(argv[1])
    claude_path = Path(argv[2])
    if not hermes_path.exists():
        print(f"Hermes routing log not found: {hermes_path}", file=sys.stderr)
        return 2
    if not claude_path.exists():
        print(
            "\n".join(
                [
                    f"Claude capture not found: {claude_path}",
                    "No live Claude Code comparison was possible.",
                    "For Claude Code subscriber sessions, ANTHROPIC_BASE_URL alone does not redirect",
                    "the main Anthropic client request path, so a simple forwarding server will not",
                    "produce this capture file.",
                ]
            ),
            file=sys.stderr,
        )
        return 2

    hermes = _load_hermes_log(hermes_path)
    claude = _load_claude_capture(claude_path)
    diffs = _compare(hermes, claude)
    if not diffs:
        print("No routing diffs detected.")
        return 0
    print("Routing diffs:")
    for diff in diffs:
        print(f"- {diff}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
