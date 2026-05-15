# Mnemo Retain Draft: Hermes Anthropic OAuth Routing

## Purpose

This file is a staging document for backfilling Mnemo once the Mnemo MCP auth
issue is fixed. It contains retain-ready memory payloads derived from the
successful Hermes Anthropic OAuth routing recovery on 2026-05-15.

Use this together with:

- [2026-05-15-anthropic-oauth-routing-runbook.md](/Users/verickwayne/.hermes/hermes-agent/docs/plans/2026-05-15-anthropic-oauth-routing-runbook.md)

## Retain Scope

- Tenant/scope: `CLI`
- Visibility: `private`
- Do not retain secrets or raw tokens
- Retain durable conclusions, working commits, and future-debugging guidance

## Suggested Retain 1

### Content

Hermes Anthropic OAuth routing was fixed on 2026-05-15 by matching current Claude Code wire behavior more closely, not by changing token class alone. Valid Bearer OAuth tokens could still route to extra usage until Hermes matched the live Claude request envelope: current claude-cli identity, sdk-cli entrypoint, beta=true query, current OAuth beta surface, current x-stainless timeout/retry headers, correct accept-encoding, valid attribution/cch wrapper, and removal of curl_cffi browser navigation headers.

### Suggested fields

- `source`: `CLI`
- `visibility`: `private`
- `importance`: `0.94`
- `source_description`: `Codex memory backfill for Hermes Anthropic OAuth routing root cause and final fix`

## Suggested Retain 2

### Content

Hermes Anthropic OAuth routing recovery commit chain on 2026-05-15: `6c152a730` aligned Hermes with the captured Claude identity and sdk-cli entrypoint, `f453a1b63` removed curl_cffi browser default headers from the real wire request, `2f0ede6e2` matched Claude OAuth beta and SDK headers, and `aa53c958c` added the canonical routing recovery runbook. Final working state used current Claude Code request shape and stopped routing to extra usage.

### Suggested fields

- `source`: `CLI`
- `visibility`: `private`
- `importance`: `0.90`
- `source_description`: `Codex memory backfill for Hermes Anthropic OAuth routing working commits`

## Suggested Retain 3

### Content

Canonical documentation for Hermes Anthropic OAuth routing recovery lives at `/Users/verickwayne/.hermes/hermes-agent/docs/plans/2026-05-15-anthropic-oauth-routing-runbook.md`. External copies were placed at `/Users/verickwayne/Projects/verick-agents/skills/agent-eval-harness/references/hermes-anthropic-oauth-routing-runbook.md` and symlinked into `/Users/verickwayne/Projects/research/hermes-anthropic-oauth-routing-runbook.md`. Use the runbook to recreate the working request shape, capture Claude vs Hermes traffic, and debug future routing regressions.

### Suggested fields

- `source`: `CLI`
- `visibility`: `private`
- `importance`: `0.82`
- `source_description`: `Codex memory backfill for Hermes Anthropic OAuth routing runbook locations`

## Suggested Retain 4

### Content

When debugging Hermes Anthropic OAuth billing classification, do not trust internal routing logs alone. Capture both real Claude traffic and Hermes outbound traffic through `scripts/capture_anthropic_proxy.py` in HTTPS proxy mode, then compare request shape. The decisive regression in this recovery was that Hermes' real wire request still carried curl_cffi browser navigation headers even after internal debug output looked close to Claude.

### Suggested fields

- `source`: `CLI`
- `visibility`: `private`
- `importance`: `0.88`
- `source_description`: `Codex memory backfill for Hermes Anthropic OAuth debugging method`

## Suggested Retain 5

### Content

Current best conclusion after the 2026-05-15 Hermes Anthropic OAuth recovery: the routing differentiator is primarily the request wrapper around the token, not the token class by itself. A fresh Hermes-minted Anthropic OAuth token may work now if sent with the corrected wrapper, but that specific case was not directly proven in-session and should be validated with live capture if attempted.

### Suggested fields

- `source`: `CLI`
- `visibility`: `private`
- `importance`: `0.84`
- `source_description`: `Codex memory backfill for Hermes Anthropic OAuth token-vs-wrapper conclusion`

## Optional Single Combined Retain

If you prefer one larger memory instead of multiple narrow ones:

### Content

Hermes Anthropic OAuth routing recovery completed on 2026-05-15. Root cause was not merely token selection; Hermes could send a valid Bearer OAuth token and still route to extra usage until the outbound request matched current Claude Code much more closely. The successful state required current claude-cli identity (`2.1.142`, `(external, sdk-cli)`), `cc_entrypoint=sdk-cli`, `?beta=true`, current Claude OAuth beta surface, current x-stainless timeout/retry headers, proper accept-encoding, valid attribution/cch wrapper, and removal of curl_cffi browser navigation headers. Key commits: `6c152a730`, `f453a1b63`, `2f0ede6e2`, `aa53c958c`. Canonical runbook: `/Users/verickwayne/.hermes/hermes-agent/docs/plans/2026-05-15-anthropic-oauth-routing-runbook.md`. External copies: harness references and `Projects/research` symlink. Best debugging method is MITM capture of both real Claude and Hermes traffic using `scripts/capture_anthropic_proxy.py`.

### Suggested fields

- `source`: `CLI`
- `visibility`: `private`
- `importance`: `0.96`
- `source_description`: `Codex combined memory backfill for Hermes Anthropic OAuth routing recovery`
