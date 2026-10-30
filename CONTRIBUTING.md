# Contributing

Use Python 3.13 and uv. Keep changes small, preserve unknown capability/usage values,
and do not add automatic provider fallback or generation retries.

```powershell
uv sync --frozen --group dev
uv run python scripts/release_version.py check
uv run ruff check src scripts tests
uv run pytest -q
node --check src/anyllama/static/app.js
uv run python scripts/verify_ui.py
```

The browser test defaults to installed Chrome. For bundled Chromium, run
`uv run python -m playwright install chromium`, then pass `--browser chromium`.
All tests use isolated data and fake providers. Keep prompts, API keys, local SQLite
files and real configuration exports out of commits and issue attachments.
No live provider key is required for CI.

Test protocol/routing/security changes with meaningful regression coverage. Ordinary
reversible copy/style edits do not need implementation-mirroring tests. Keep UI assets
local; provider profiles and key requirements belong in the existing profile registry.

Release/version instructions: [docs/RELEASING.md](docs/RELEASING.md).
Changes are distributed under the repository's MIT license.
