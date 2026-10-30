# Validation — 2026-10-30

Target IDE found on this machine: **Visual Studio Community 2026 18.11.2**.
Implementation/build runtime: **CPython 3.13.18, Windows x64**.

## Executed

| Check | Result | Evidence / limits |
|---|---|---|
| Python protocol, provider, storage, administration, metrics, security, updates and release tooling | **146 passed** | `python -m pytest -q`; controlled upstream fixtures |
| Real downstream HTTP disconnect | **Passed** | Local Uvicorn + HTTP client; upstream closes and Activity records 499 |
| Python lint | **Passed** | `ruff check src scripts tests` |
| JavaScript syntax | **Passed** | `node --check src/anyllama/static/app.js` |
| Browser acceptance | **Passed** | Headless Chrome; 16 scenarios including updates, provider selection, optional keys, onboarding, pricing, metrics and the Ollama port preset; no browser console errors |
| Standalone EXE build | **Passed** | PyInstaller one-file Windows x64 build |
| EXE API and bundled dashboard assets | **Passed** | Isolated data directory; Python and virtual environment removed from child process PATH |
| Windows Credential Manager through EXE | **Passed** | Temporary dummy credential saved, read and deleted; no real provider key used |
| EXE duplicate process and restart persistence | **Passed** | Second launch exits without replacing the active instance; saved theme survives restart |
| EXE tray process startup | **Passed** | Starts with the actual Windows tray backend, without opening a browser |
| EXE v0.2.2 default port | **Passed** | Isolated first launch without `--port` listens on 11434; duplicate launch, restart and tray tested on the same port |
| TensorX live generation | **Passed** | Running v0.1.1 → bare `/chat/completions` → configured Qwen model; HTTP 200, response “OK”, provider usage returned |
| VS Code | **User confirmed** | User reports normal operation; no independent IDE automation claimed |
| TensorX pricing metadata | **Read live** | `/model/info` contains per-token input/output/cache-read rates; no paid generation used for v0.2.0 metrics validation |
| Metrics v0.2.0 | **Passed with fixtures** | Reported/estimated/free/unknown cost, cache accounting, reasoning subset, timed stream/tool fragments, native duration, cancellation/error semantics and v1 migration |
| New v0.3.0 profiles | **Passed with fixtures** | Ten cloud + five local profiles: add/check/enable, chat, SSE, tool call and follow-up; optional local keys, Anthropic pagination, Gemini signed replay protection and usage variants |
| Update checks | **Passed with fixtures** | Numeric version comparison, stable/ready assets, cache persistence, safe links, bounded metadata, errors, cancellation and admin/CSRF protection |
| Release workflows | **Static validation passed; hosted rerun pending** | actionlint 1.7.12; first hosted CI run exposed a test import-path issue, reproduced and fixed locally with explicit pytest `pythonpath` |

Browser scenarios: first-run wizard, add/check provider, enable model, configure metadata,
add manual model, search, change light/dark theme, pause/resume, navigate all views,
and a narrow viewport with no horizontal page overflow. Pricing overrides and real proxy
calls against a fake provider exercise the Activity table. Screenshots in `screenshots/`
contain **sample provider data**, not results from a real account.

v0.1.1 adds regression coverage for the bare `/chat/completions` VS route, HTTPS requirements,
limits on declared/chunked bodies including admin routes, typed catalog data and invalid model
references. The browser check includes escaped legacy token-limit metadata. The user's supplied
VS screenshot confirms model discovery; live generation and Agent behavior remain pending.

Protocol scenarios cover fragmented UTF-8/SSE/NDJSON transport, ordinary and streaming
responses, same-name parallel tool calls, deterministic call IDs, tool results in a later
turn, usage frames, separate thinking output, preserved opaque OpenAI replay state,
explicit rejection of incompatible replay, malformed arguments, HTTP 400/401/402/403/404/429/500,
mid-stream errors, incomplete responses, timeouts, provider name collisions and stale catalogs.

One dependency warning remains: Starlette deprecates its current HTTPX TestClient adapter
in favor of HTTPX2. The tests pass; the production transport uses HTTPX directly.

## Pending live acceptance

These are not claimed as verified:

- Real account access and generations through OpenRouter and Ollama Cloud.
- Chat from the actual Visual Studio UI (discovery is observed in the user's screenshot).
- Actual VS Agent execution of its own repository tools across multiple turns.
- Provider-dependent image/reasoning compatibility and cancellation from the VS UI.
- Manual inspection of the tray menu and opt-in Windows autostart after sign-in.

The supplied VS tooltip and UI inspection confirm that the disabled model picker is gated
by the IDE's chat-session type: “Your own models work only in Agent (Preview). Switch or
create a new thread to use them.” The saved endpoint is `http://127.0.0.1:11435`, models are
enabled/selected in VS configuration, and live proxy generation succeeds independently of VS.
After enabling **Use new Copilot chat experience** and restarting VS, UI inspection found
`EditorPreviewFeaturesBlockedBanner`: **“Your active GitHub account is blocked from using
preview Copilot features. Change accounts or disable the new Copilot experience to continue.”**
This is an account feature restriction. Check Editor preview features in the signed-in
account's GitHub Copilot settings, or ask the organization's administrator about policy.
An organization policy is a possible cause, not a verified fact here. No account settings
were changed automatically. IDE generation has not been claimed as verified.

Real-provider checks require API keys configured locally in the application. Do not paste
keys into chat, documentation or test fixtures. A tool-capable model's advertised metadata
does not certify its behavior in Visual Studio Agent.

## Reproduce automated checks

```powershell
uv sync --frozen --group dev
uv run pytest -q
uv run ruff check src scripts tests
node --check src/anyllama/static/app.js
uv run python scripts/verify_ui.py
uv run python scripts/build.py
uv run python scripts/verify_exe.py
```

`verify_ui.py` requires installed Chrome and uses a fake provider and in-memory secrets.
`verify_exe.py` starts only its own EXE processes, creates isolated test data, briefly writes
one dummy Windows credential and removes it. It forcibly stops its own test processes
after checking restart recovery. Neither script changes autostart or makes provider API calls.

## Live Visual Studio acceptance checklist

1. Exit any previous AnyLlama instance, then start `dist/0.4.0/AnyLlama.exe`. Add a real provider
   (or keep your existing connections) and run **Check & refresh**.
2. Enable a model with Tools support. Confirm its context limit in **Models → Configure**.
3. In VS 18.11.2, open **Chat → Manage Models → Add model provider → Ollama** and use
   `http://127.0.0.1:11434` (or the endpoint currently shown by AnyLlama).
4. Select the exposed model and send a short prompt. Confirm streamed output and an
   Activity entry containing that provider and model.
5. In a disposable solution, ask Agent to inspect two files and summarize their contents.
   Verify tool calls, results and the next assistant turn. Then ask it to make a small edit
   and run the solution's test command; inspect the resulting changes yourself.
6. Stop a long generation. Confirm the upstream request ends and Activity records cancellation.
7. Enable a model on another provider, refresh the VS model list, switch models and repeat.
8. Check the tray Pause/Resume/Exit commands. Opt into autostart only if wanted, then confirm
   it starts quietly at the next sign-in and can be disabled from Settings.

If a model fails, record the VS build, provider, model ID, selected protocol and Activity
error category. Never attach raw prompts, credentials or proprietary replay state by default.
