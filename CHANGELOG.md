# Changelog

## 0.4.2 — 2026-10-31

- Clarify how to add AnyLlama as an Ollama provider in Visual Studio and reload its model list.

## 0.4.1 — 2026-10-31

- Allow local providers such as LM Studio to use HTTP on private network IP addresses,
  with a warning that network traffic is unencrypted.

## 0.4.0 — 2026-10-30

- Check the public GitHub repository for stable Windows releases, automatically once a day
  or manually in Settings. New versions link to GitHub for manual download and installation.
- Add Windows CI, a version-bump pull request workflow, and tested EXE/ZIP GitHub Releases
  with SHA-256 checksums. Add an MIT license and contributor/release documentation.

## 0.3.0 — 2026-10-30

- Add OpenAI, Anthropic, Gemini, DeepSeek, xAI, Mistral, Groq, Together, Fireworks and Cerebras profiles.
- Add optional-key local profiles for LM Studio, FastFlowLM, Jan, Lemonade and oMLX.
- Handle provider catalog differences, Groq usage and FastFlowLM decoding speed.
- Explain and guard incompatible Gemini tool replay over Ollama.

## 0.2.2 — 2026-10-30

- Use Ollama's standard port 11434 by default. Keep explicitly saved ports unchanged.

## 0.2.0 — 2026-10-30

- Show request throughput, token usage and reported/estimated USD cost in Activity.
- Preserve historical request records while adding nullable metrics.

## 0.1.1 — 2026-10-30

- Support Visual Studio's bare `/chat/completions` route, improve request safeguards and add llama branding.

## 0.1.0 — 2026-10-30

- Initial local proxy, provider/model configuration, protocol conversion, dashboard and Windows tray app.
