# Design and security review — v0.1.1, 2026-10-30

## Relationship to the reference proxy

The core idea matches [Sander's PowerShell proxy](https://github.com/sander-/openrouter-copilot-proxy/blob/master/openrouter-proxy.ps1):
advertise an Ollama endpoint to Visual Studio, discover remote models, and forward generations
through a shared HTTP client. Neither implementation runs a separate agent or executes tools.

AnyLlama is larger because the approved scope includes multiple providers, a dashboard, model
selection, Credential Manager, SQLite state and a Windows tray application. The reference's
chat routes share an OpenAI request/SSE forwarding handler. AnyLlama also converts genuine
Ollama messages and NDJSON, including tool-call history, rather than treating both wire formats
as identical. It advertises actual/overridden capabilities instead of claiming tools for all models.

[Issue #3](https://github.com/sander-/openrouter-copilot-proxy/issues/3) identifies a Visual Studio
compatibility requirement: generation can arrive at **`/chat/completions` without `/v1`**.
v0.1.1 adds that path to the same handler as `/v1/chat/completions`, with streaming and ordinary
response regression tests. No duplicate forwarding implementation is introduced.

The user's screenshot confirms that their Visual Studio discovers exposed TensorX models and
shows tools/vision/context metadata. It does not establish successful generation or Agent tool
execution. The `unknown` suffix refers to missing parameter-size metadata, not a missing model.
We do not fabricate a parameter count, GGUF format or quantization for remote services.

## Honest assessment

| Principle | Assessment |
|---|---|
| ULTRA-KISS | **No.** The core is a direct proxy, but the complete desktop product is broader than a one-file bridge. Simplifying it to that level requires reducing the agreed feature set. |
| DRY | **Mostly, with room to improve.** Providers share transport, model routing and streaming machinery. The VS alias shares one handler. Icon geometry now has one source for SVG, tray and EXE. Some exception handling and generation-option mappings still repeat. |
| YAGNI | **Reasonable for the approved scope.** No plugin framework, local inference engine, embeddings, LAN accounts, automatic fallback or updater was added. The UI/tray/catalog work was explicitly requested. |
| Maintainability | **A useful baseline, not finished cleanup.** Protocol tests and separated storage/adapters/codecs help. The large `create_app` function and HTML strings in JavaScript are the main maintenance costs. Separating admin and inference routing would be a useful focused follow-up. |
| OWASP safety | **Partial coverage; no blanket certification.** Specific controls and regression tests exist, but neither a dependency scan nor unit tests prove full application security. |

## Concrete findings fixed in v0.1.1

1. **Remote plaintext API URLs:** `ProviderInput` previously accepted HTTP to arbitrary hosts,
   allowing credentials and prompts to be sent without TLS after misconfiguration. Remote API
   URLs now require HTTPS, including saved connections checked again before use. HTTP remains
   available for literal loopback addresses and localhost.
2. **Late request-size limit:** inference checked the size after buffering the entire body, and
   framework-parsed admin bodies did not use that check. A bounded ASGI reader now rejects
   oversized declared or chunked bodies with HTTP 413 before framework parsing, across routes.
3. **Untrusted model limit interpolation:** the model editor inserted provider-sourced token limits
   into input attributes without escaping. Both fields now use HTML escaping, and catalog models
   are validated before persistence. Invalid catalogs preserve the last usable data. Browser
   verification also exercises a legacy malformed record. CSP remains an additional defense.
4. **Malformed model references:** `/api/show` with a non-string model previously raised HTTP 500.
   It now returns an explicit HTTP 400 client error.

## OWASP Top 10:2025 mapping

Reference: [OWASP Top 10:2025](https://top10.owasp.org/2025/). This mapping is a review of this
local application, not a claim of passing an external assessment.

| Category | Implemented controls and practical limits |
|---|---|
| A01 Access control | Administration session + CSRF; loopback socket; Host/Origin checks. Inference deliberately trusts other local processes, as Ollama does. Not suitable for shared or hostile hosts. |
| A02 Configuration | Local bind only, no public listen setting, restrictive dashboard CSP, local assets, redirects disabled upstream. Custom URLs are an intentional administrator capability. |
| A03 Supply chain | Locked dependency versions; pip-audit found no known vulnerabilities in 44 installed distributions. The editable project itself was skipped; vendored HTMX and application code are outside that scan. Ongoing audit automation and signed releases remain absent. |
| A04 Cryptography | Windows Credential Manager for keys, normal TLS verification, HTTPS for remote providers. Bootstrap/session protection assumes the user's data directory and OS account are trusted. |
| A05 Injection | Parameterized SQL, Jinja autoescape, escaped browser values, typed catalog validation. No model tool execution or shell command construction from prompts. Dynamic HTML still needs care in future changes. |
| A06 Design | Explicit selected-provider routing, no automatic generation retries, bounded request bodies, disconnect cancellation. Aggregate concurrency and provider response/frame sizes are not yet bounded. |
| A07 Authentication | Random bootstrap/session/CSRF tokens; constant-time token comparison; HttpOnly and SameSite=Strict cookie; session lifetime. The inference API intentionally has no separate login. |
| A08 Integrity | Dependency lock hashes, static resources in the executable, no updater or runtime plugin loader. The EXE is not Authenticode-signed. |
| A09 Logging | Status, latency, usage and error categories; bounded activity history; no prompts, keys or raw provider error bodies. No enterprise monitoring/alert service. |
| A10 Exceptions | Explicit errors for invalid input, provider failures, partial streams and missing credentials; cancellation regression test. This is not an exhaustive fault-injection assessment. |

## Evidence and remaining work

- Python dependency audit JSON: [DEPENDENCY-AUDIT.json](DEPENDENCY-AUDIT.json). No known
  vulnerabilities were reported; this statement applies only to the versions checked that day.
- Test and release evidence: [VALIDATION.md](VALIDATION.md).
- Remaining security improvements: cap provider response/frame/tool-argument sizes and concurrent
  generations; automate dependency auditing; test adversarial input more broadly.
- Remaining product acceptance: actual generation, cancellation and multi-turn Agent execution
  in VS 18.11.2. Discovery is observed in the supplied screenshot; those later stages are pending.
- Live TensorX generation through the v0.1.1 bare Chat Completions endpoint returned HTTP 200
  and “OK”. The IDE's own tooltip attributes disabled models to a legacy chat session, and the
  user confirmed its new Copilot chat experience setting is off. This is distinct from proxy routing.
