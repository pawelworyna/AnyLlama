# Architecture

```text
Visual Studio → Ollama JSON/NDJSON ─┐
                                 ├─ model alias → adapter → provider
Other clients → OpenAI JSON/SSE ──┘
                        ↑
              Local administration UI
```

`app.py` owns HTTP boundaries, administration sessions, request lifetime and diagnostics.
`codecs.py` converts native Ollama messages to a Chat Completions-shaped intermediate
representation. OpenAI-to-OpenAI requests retain provider-specific message fields.
`providers.py` owns authentication, HTTP transport, catalog discovery and stream decoding.
`storage.py` owns transactional SQLite state; `secrets.py` explicitly selects WinVault.
`desktop.py` owns the process lock, loopback socket, tray lifecycle and optional autostart.
`security.py` bounds request bodies before parsing. `branding.py` is the shared source for
the purple llama SVG, tray image and executable icon.

`/chat/completions` and `/v1/chat/completions` share the same handler; Visual Studio
can use the former when connecting through its Ollama provider.

## Provider contract

Adapters implement `catalog`, `prepare`, `open_chat`, `completion` and `chunks`.
The adapter's stream is a sequence of Chat Completions chunk objects. Catalog entries
carry the exact upstream identifier, display name and nullable capability/limit values.
OpenRouter uses `/api/v1`, TensorX uses `/v1`, Ollama uses native `/api/*`, and custom
connections append `/models` or `/chat/completions` to their configured base URL.

The public alias is `<connection-id>/<exact-upstream-id>`. Routing uses the registered
alias, not arbitrary user-supplied URLs. An appended `:latest` is resolved only if there
is no exact registered match. Rename and metadata edits do not change the public alias.

## Streaming and tools

HTTP status is checked before sending downstream streaming headers. SSE decoding handles
comments, UTF-8 split across network chunks, multiple data lines and a final frame without
a newline. Native Ollama streams are parsed as NDJSON. Provider finish markers are
required; a premature EOF produces an error, not a successful completion.

Text and reasoning flow immediately. Tool arguments accumulate by call index until they
form complete JSON. They are emitted once as native Ollama tool calls; the final native
frame does not repeat the tool calls or previously streamed text. Streaming retains only
tool state and usage, not the full generated conversation.

Native histories without call IDs get deterministic IDs derived from the assistant turn,
call position, function name and normalized arguments. Results with IDs retain their
correlation; results with names use unmatched calls in order. Missing or orphaned results
are rejected. Tool execution remains the client's responsibility.

Signed/encrypted reasoning is preserved on the OpenAI route. Conversion to Ollama rejects
opaque replay state explicitly; ordinary text/summary reasoning is converted to thinking.
No prompt/response is written to Activity or the log. Diagnostics retain error categories,
not upstream response bodies. Unknown usage stays unknown.

## Local lifecycle

The desktop acquires a per-data-directory OS lock and binds a loopback socket before
starting Uvicorn. A second launch opens the existing dashboard. A private instance file
allows the launcher to recover the dashboard bootstrap link after a second launch.
The fragment token is exchanged for an HttpOnly, SameSite=Strict administration session,
then removed from browser history. Mutations require a CSRF header.

The inference API deliberately has no user login, matching local Ollama. Trusted Host
validation and rejection of foreign browser origins also apply to inference requests.
The application has no public listen option. Pause rejects new generations and leaves
existing streams and the administration UI available.

SQLite stores settings, providers, model overrides and up to 1,000 activity summaries.
The UI returns the latest 100 summaries. Refreshes never overwrite manual overrides or
model selections. Refresh failures retain the last catalog and expose a timestamp/error.
Deletion and refresh of the same connection are serialized. Credentials live separately
in Windows Credential Manager; missing or locked credentials produce an explicit error.

`metrics.py` computes a fixed numeric request summary from provider usage, native generation
duration or measured first/last stream fragments. Pricing is catalog metadata with the same
manual override rules as capabilities. Reported USD cost takes precedence over an estimate;
reasoning is a subset of output usage and cache is a subset of input. No generation retries,
benchmark calls or per-request accounting lookup is performed. Only whitelisted measurements
enter SQLite; arbitrary usage extensions and conversation data are not retained. Activity
schema v2 adds nullable columns, preserving v1 rows and marking unavailable metrics unknown.

Provider profiles live in one registry (`models.PRESETS`), used for input validation and
dashboard labels, URLs, key requirements, marks and compatibility notes. New cloud and
local profiles share `OpenAIAdapter`; optional local keys use the same vault path as
cloud keys. Anthropic adds a version header and cursor pagination, Together's array
catalog is normalized, and known catalog capability fields are read without guessing
from model names. Gemini tools require the OpenAI wire format to preserve signed replay.

`UpdateChecker` uses the shared asynchronous transport without provider authentication.
It reads only the fixed public GitHub repository's latest stable release metadata, caps
the response at 256 KiB, validates the stable tag and matching uploaded ZIP, and constructs
its own GitHub link. A separate SQLite cache survives restarts (schema v3); successful
checks have a 24-hour TTL, failures an hour, and manual checks a one-minute cooldown.
Checks run outside inference. There is no executable download, installer or updater process.
