# Hermes Anthropic OAuth Routing Runbook

## Purpose

This document records the end-to-end Anthropic OAuth routing work that turned
Hermes from "valid OAuth token, but billed as extra usage / third-party" into
"routes like Claude Code and consumes the intended Claude subscription path."

It is written for an agent with no prior context. If you need to reproduce,
repair, or extend this work on another Hermes checkout, start here.

## Bottom Line

The working path was **not** unlocked by "finding a magic token". The decisive
factor was making Hermes' **actual outbound request** match current Claude Code
closely enough on the wire.

The final conclusion is:

- A Claude/Anthropic OAuth Bearer token by itself was **not sufficient**.
- Hermes could already send a valid Bearer token and still route to extra
  usage.
- The successful fix came from matching Claude Code's **request wrapper**:
  headers, query params, attribution block shape, SDK metadata, and the
  absence of curl_cffi's browser navigation headers.
- The token still matters, but the request envelope was the differentiator.

## Symptom

Hermes Anthropic OAuth requests were accepted by Anthropic, but the traffic was
classified as third-party / extra-usage billing instead of Claude Code style
first-party subscription routing.

Observed failure pattern:

- Hermes could authenticate successfully.
- Hermes could get completions successfully.
- Billing still landed in the wrong bucket.

## Working Conclusion

The request that finally worked had all of the following properties:

- Bearer token from the Claude Code / Anthropic OAuth path
- `POST https://api.anthropic.com/v1/messages?beta=true`
- `User-Agent: claude-cli/2.1.142 (external, sdk-cli)`
- `x-app: cli`
- `anthropic-dangerous-direct-browser-access: true`
- Current Claude Code OAuth beta surface
- `x-stainless-timeout: 600`
- `x-stainless-retry-count: 0`
- `x-stainless-*` Node identity values matching current Claude Code capture
- `accept-encoding: gzip, deflate, br, zstd`
- No browser navigation headers from curl_cffi impersonation
- First system block:
  `x-anthropic-billing-header: cc_version=2.1.142.<fingerprint>; cc_entrypoint=sdk-cli; cch=<hash>;`
- Valid `cch` and fingerprint wrapper around the request body

## What Was Wrong Before

Earlier Hermes patches got partway there, but still missed critical wire-level
details.

Key wrong assumptions / mismatches:

1. `2.1.87` was pinned as if the `cch` / fingerprint logic required it.
   That turned out to be stale. The algorithm still worked for current Claude
   Code once the version string was updated.

2. Matching only the Bearer auth form was not enough.
   Hermes could send:
   - `Authorization: Bearer ...`
   - Claude-ish `User-Agent`
   - attribution block
   and still route incorrectly.

3. Hermes' internal routing debug log was not enough to prove wire parity.
   We had to capture Hermes' actual outbound HTTPS request through a MITM proxy.

4. curl_cffi browser impersonation was helping at the TLS/H2 layer but also
   leaking incorrect request headers:
   - `sec-ch-ua`
   - `sec-fetch-*`
   - `upgrade-insecure-requests`
   - `accept-language`
   - `priority`

   Real Claude Code did **not** send those. This was a major hidden mismatch.

5. Hermes was missing current Claude Code SDK/per-request headers:
   - `x-stainless-timeout: 600`
   - `x-stainless-retry-count: 0`

6. Hermes was still using an older/staler OAuth beta surface instead of the
   current captured Claude list.

## What Actually Differentiated the Successful State

The fixes that mattered, in order of confidence:

1. **Remove curl_cffi browser navigation headers**
   This was the largest wire mismatch discovered during direct Hermes capture.

2. **Match current Claude OAuth beta surface**
   Hermes originally sent a much smaller beta list than live Claude Code.

3. **Match current SDK metadata**
   Especially:
   - `x-stainless-timeout: 600`
   - `x-stainless-retry-count: 0`
   - `x-stainless-package-version: 0.94.0`
   - `x-stainless-runtime-version: v24.3.0`

4. **Use current Claude identity**
   - version `2.1.142`
   - `(external, sdk-cli)` user agent
   - `cc_entrypoint=sdk-cli`
   - `?beta=true`

5. **Keep the attribution wrapper correct**
   - fingerprint
   - `cch`
   - ordering and formatting

## Token vs Wrapper

### What this work proves

- The **wrapper mattered**.
- We observed states where Hermes used the same class of OAuth Bearer token and
  still routed to extra usage.
- Hermes only started working after the request shape was corrected.

### What this work does not prove

- It does **not** prove that any arbitrary OAuth token will work.
- It does **not** prove that only the exact current macOS Keychain token can
  work.

### Best current interpretation

- Anthropic is likely classifying based on **token + request envelope**.
- The token must be from the correct OAuth/auth product family.
- The request envelope must look like Claude Code closely enough.

## Should a Fresh Hermes-Minted OAuth Token Work?

### Current expectation

Probably **yes, or at least it has a real chance now**.

Reason:

- Hermes' native OAuth flow uses the same Anthropic client ID family and scope
  pattern:
  - `client_id = 9d1c250a-e61b-44d9-88ed-5944d1962f5e`
  - scopes:
    `org:create_api_key user:profile user:inference`
- The major failure mode we actually fixed was the request wrapper, not merely
  token selection.

### Important caveat

This specific session did **not** directly prove that a freshly created
Hermes-native OAuth token works. The working validation path used Claude Code /
Anthropic OAuth credentials already present on the machine.

So the strongest honest statement is:

- Before these fixes: a new Hermes-minted token was unlikely to help.
- After these fixes: a new Hermes-minted token is now plausible, but still
  needs direct validation.

## Canonical Files Touched

These files define the working routing behavior:

- `agent/claude_code_identity.py`
- `agent/anthropic_adapter.py`
- `agent/curl_cffi_transport.py`
- `scripts/capture_anthropic_proxy.py`
- `scripts/compare_anthropic_routing.py`

Supporting regression tests:

- `tests/agent/test_anthropic_adapter.py`
- `tests/agent/test_curl_cffi_transport.py`
- `tests/scripts/test_capture_anthropic_proxy.py`

## Commit Trail

Key commits in this routing recovery:

1. `62c91bf27`
   Capture Claude Code routing via HTTPS proxy

2. `6c152a730`
   Align Anthropic OAuth routing with Claude capture

3. `f453a1b63`
   Disable browser default headers in curl_cffi transport

4. `2f0ede6e2`
   Match Claude OAuth beta and SDK headers

If you are reconstructing the work on another checkout, these commits are the
minimal history to inspect.

## Final Working Request Shape

The final Hermes wire capture that aligned with the successful state included:

### URL

`https://api.anthropic.com/v1/messages?beta=true`

### Stable headers

- `authorization: Bearer <oauth token>`
- `user-agent: claude-cli/2.1.142 (external, sdk-cli)`
- `accept: application/json`
- `accept-encoding: gzip, deflate, br, zstd`
- `content-type: application/json`
- `x-stainless-lang: js`
- `x-stainless-package-version: 0.94.0`
- `x-stainless-os: MacOS`
- `x-stainless-arch: arm64`
- `x-stainless-runtime: node`
- `x-stainless-runtime-version: v24.3.0`
- `x-stainless-timeout: 600`
- `x-stainless-retry-count: 0`
- `anthropic-beta: claude-code-20250219,oauth-2025-04-20,context-1m-2025-08-07,interleaved-thinking-2025-05-14,context-management-2025-06-27,prompt-caching-scope-2026-01-05,advisor-tool-2026-03-01,advanced-tool-use-2025-11-20,effort-2025-11-24,afk-mode-2026-01-31,extended-cache-ttl-2025-04-11,cache-diagnosis-2026-04-07`
- `anthropic-dangerous-direct-browser-access: true`
- `x-app: cli`
- `anthropic-version: 2023-06-01`

### Dynamic headers

These vary run to run and should not be treated as regressions by themselves:

- `x-claude-code-session-id`
- `x-client-request-id`
- `content-length`

### Attribution block

The first system block must be:

`x-anthropic-billing-header: cc_version=2.1.142.<fingerprint>; cc_entrypoint=sdk-cli; cch=<hash>;`

Where:

- `<fingerprint>` is derived from the first user message text and version
- `<hash>` is the `cch` computed from the serialized body with the placeholder
  in place

## Why `2.1.87` Was Not the Real Requirement

Hermes originally pinned the claimed Claude Code version to `2.1.87` because
the `cch` and fingerprint reverse-engineering had been validated there.

That turned out to be too conservative.

During live validation:

- The captured Claude request used `2.1.142`
- Hermes' fingerprint code produced the same fingerprint suffix for the same
  prompt under `2.1.142`
- Therefore the algorithm, salt, and versioned wrapper still worked when
  updated to the current live version

So:

- `2.1.87` was a stale snapshot
- the real requirement was correct **algorithm + current version alignment**

## How to Reproduce the Investigation from Scratch

### 1. Confirm Claude CLI auth mode

Do this before trusting any capture:

```bash
claude auth status
```

If the shell has `ANTHROPIC_API_KEY` set, Claude may bypass OAuth and use an
API key instead. That was a major confounder during this work.

Check:

```bash
python3 - <<'PY'
import os
for k in ["ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]:
    print(k, "set" if os.getenv(k) else "unset")
PY
```

When capturing true Claude Max OAuth traffic, run Claude with:

```bash
env -u ANTHROPIC_API_KEY claude auth status
```

Expected OAuth-like output includes:

- `authMethod: "claude.ai"`
- `apiProvider: "firstParty"`
- account email/org/subscription fields populated

### 2. Capture real Claude traffic

Start proxy:

```bash
python3 scripts/capture_anthropic_proxy.py --mode https-proxy
```

Then run Claude through it:

```bash
env -u ANTHROPIC_API_KEY \
  HTTPS_PROXY=http://127.0.0.1:8788 \
  https_proxy=http://127.0.0.1:8788 \
  SSL_CERT_FILE=/tmp/hermes-anthropic-mitm/ca.pem \
  NODE_EXTRA_CA_CERTS=/tmp/hermes-anthropic-mitm/ca.pem \
  claude -p "Reply with exactly OK." --output-format text
```

The capture is written to:

- `/tmp/claude-routing-capture.json`

### 3. Capture Hermes' actual outbound wire request

Do **not** rely only on Hermes' internal routing debug output.

Start another proxy:

```bash
python3 scripts/capture_anthropic_proxy.py \
  --mode https-proxy \
  --listen-port 8790 \
  --capture-file /tmp/hermes-routing-wire-capture.json
```

Run a real Hermes Anthropic request through it:

```bash
env -u ANTHROPIC_API_KEY \
  HTTPS_PROXY=http://127.0.0.1:8790 \
  https_proxy=http://127.0.0.1:8790 \
  SSL_CERT_FILE=/tmp/hermes-anthropic-mitm/ca.pem \
  NODE_EXTRA_CA_CERTS=/tmp/hermes-anthropic-mitm/ca.pem \
  HERMES_ANTHROPIC_ROUTING_DEBUG=1 \
  HERMES_ANTHROPIC_ROUTING_DEBUG_FILE=/tmp/hermes-anthropic-routing.log \
  venv/bin/python - <<'PY'
from agent.anthropic_adapter import build_anthropic_client, resolve_anthropic_token

token = resolve_anthropic_token()
client = build_anthropic_client(token)
resp = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=8,
    messages=[{"role": "user", "content": "Reply with exactly OK."}],
)
parts = getattr(resp, "content", []) or []
texts = [getattr(part, "text", "") for part in parts if getattr(part, "type", "") == "text"]
print("".join(texts).strip())
PY
```

### 4. Compare

Hermes internal intent vs Hermes wire:

```bash
python3 scripts/compare_anthropic_routing.py \
  /tmp/hermes-anthropic-routing.log \
  /tmp/hermes-routing-wire-capture.json
```

Hermes internal intent vs real Claude capture:

```bash
python3 scripts/compare_anthropic_routing.py \
  /tmp/hermes-anthropic-routing.log \
  /tmp/claude-routing-capture.json
```

Use the raw capture JSON files directly when deeper diffing is needed.

## From-Scratch Patch Checklist

If you need to port this working state onto another Hermes instance, do the
following in order:

1. Update `agent/claude_code_identity.py`
   - set claimed version to the current captured Claude Code version

2. Update `agent/curl_cffi_transport.py`
   - keep TLS/H2 impersonation
   - inject the attribution block
   - compute valid `cch`
   - disable curl_cffi default browser headers with `default_headers=False`
   - strip incoming `accept-encoding` so curl_cffi can emit the intended value
   - preserve `x-stainless-timeout` and `x-stainless-retry-count`

3. Update `agent/anthropic_adapter.py`
   - use OAuth Bearer auth
   - set `default_query={"beta": "true"}`
   - set current Claude Code user agent and entrypoint shape
   - set current Claude OAuth beta list
   - set current Node SDK identity headers
   - set:
     - `x-stainless-timeout: 600`
     - `x-stainless-retry-count: 0`
   - ensure API-key fallback is suppressed for OAuth path

4. Validate against real Claude capture
   - do not trust local reasoning alone
   - use HTTPS MITM capture

5. Run focused tests
   - `tests/agent/test_anthropic_adapter.py`
   - `tests/agent/test_curl_cffi_transport.py`
   - `tests/scripts/test_capture_anthropic_proxy.py`

## Testing Commands Used in This Recovery

Focused tests:

```bash
venv/bin/python -m pytest \
  tests/agent/test_anthropic_adapter.py \
  tests/agent/test_curl_cffi_transport.py \
  tests/scripts/test_capture_anthropic_proxy.py \
  -q
```

Short transport-only regression:

```bash
venv/bin/python -m pytest tests/agent/test_curl_cffi_transport.py -q
```

## Public Doc Caveat

At the time of this runbook, the public provider documentation under:

- `website/docs/integrations/providers.md`

still described the Anthropic OAuth path in a way that did **not** capture the
actual routing requirements discovered here. Treat this runbook as the source
of truth for low-level routing behavior unless and until the public docs are
updated to match.

## What To Try Next If It Breaks Again

If routing regresses in the future, check these in order:

1. Has Claude Code changed version, user agent shape, or entrypoint again?
2. Has the live Claude OAuth beta surface changed?
3. Has Claude changed `x-stainless-*` values?
4. Did curl_cffi start adding different default headers?
5. Did the `cch` / fingerprint algorithm inputs change?
6. Is Claude CLI actually using OAuth, or did the shell inject
   `ANTHROPIC_API_KEY` and switch it to API-key mode?
7. Did Hermes stop reading the intended OAuth credential source?

## Practical Recommendation

If the goal is "make Hermes behave like Claude Code against Anthropic OAuth",
treat this as a **live parity problem**, not a static auth problem.

The right debugging hierarchy is:

1. confirm token source
2. capture real Claude traffic
3. capture Hermes wire traffic
4. diff the actual requests
5. patch Hermes to reduce stable mismatches

Do not assume a valid token implies correct billing classification.
