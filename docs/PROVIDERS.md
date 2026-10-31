# Provider profiles — v0.3.0

AnyLlama has 18 named profiles and Custom OpenAI-compatible. Ten cloud profiles were added
as a practical selection of popular APIs, not a measured market-share ranking. Profiles
fill the API URL and choose small protocol differences; no provider SDK is installed.
All new profiles use the shared Chat Completions adapter. Ollama Cloud retains its native API.

## Cloud APIs

| Profile | Default API base | Protocol reference |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | [Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) |
| Anthropic (Claude) | `https://api.anthropic.com/v1` | [OpenAI SDK compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk) |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai) |
| DeepSeek | `https://api.deepseek.com/v1` | [API setup](https://api-docs.deepseek.com/en/) |
| xAI (Grok) | `https://api.x.ai/v1` | [Chat Completions](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions) |
| Mistral AI | `https://api.mistral.ai/v1` | [Migration and compatibility](https://docs.mistral.ai/resources/migration-guides) |
| Groq | `https://api.groq.com/openai/v1` | [OpenAI compatibility](https://console.groq.com/docs/openai) |
| Together AI | `https://api.together.ai/v1` | [OpenAI compatibility](https://docs.together.ai/docs/inference/openai-compatibility) |
| Fireworks AI | `https://api.fireworks.ai/inference/v1` | [OpenAI compatibility](https://docs.fireworks.ai/tools-sdks/openai-compatibility) |
| Cerebras | `https://api.cerebras.ai/v1` | [OpenAI compatibility](https://inference-docs.cerebras.ai/resources/openai) |

OpenRouter, Ollama Cloud and TensorX remain available with their existing IDs and adapters.
Cloud profiles require a provider API key, stored in Windows Credential Manager.
Authentication, available models and billing depend on the provider account.

## Local servers

Start the upstream application and its API server before checking the connection in AnyLlama.
Download/load a chat model there. AnyLlama does not install or start these applications.
Keys are optional in AnyLlama; supply the server's configured key when authentication is enabled.
Addresses can be edited while adding a connection, including the port and API prefix.

| Profile | Default API base | Setup reference |
|---|---|---|
| LM Studio | `http://127.0.0.1:1234/v1` | [Local OpenAI API](https://lmstudio.ai/docs/developer/openai-compat) |
| FastFlowLM | `http://127.0.0.1:52625/v1` | [Server setup](https://fastflowlm.com/docs/instructions/server/webui/) |
| Jan | `http://127.0.0.1:1337/v1` | [Local API Server](https://docs.jan.ai/desktop/api-server) |
| Lemonade | `http://127.0.0.1:13305/v1` | [OpenAI API](https://lemonade-server.ai/docs/api/openai/) |
| oMLX | `http://127.0.0.1:8000/v1` | [Project documentation](https://github.com/jundot/omlx) |

Jan's **Execute Tools on Server** should stay disabled: the IDE executes Agent tools.
Older Lemonade releases may use a different port or `/api/v1` prefix; use the address
shown by that installation. oMLX requires Apple Silicon/macOS. For the Windows AnyLlama
application, use its HTTPS API endpoint or its private IP address. For LM Studio,
enter `http://<server-private-ip>:1234/v1` and replace the placeholder with the
server's actual address. The preset does not automatically
discover another computer. HTTP is also supported for loopback and private IPv4/IPv6
addresses; it is unencrypted, including prompts and API keys. Public endpoints require
HTTPS. AnyLlama itself still listens only on loopback; no network listener is created.

## Compatibility and metadata

- Only chat models are supported by this proxy. OpenAI Responses-only, embeddings, audio
  and image-generation endpoints are outside scope. A provider catalog can include such
  models; do not enable them for chat. Explicit non-chat Mistral/Together entries are filtered.
- Tool, vision, reasoning and context information come from catalog fields where available.
  Many standard `/models` responses omit capabilities and prices. Unknown stays unknown;
  configure these values in **Models → Configure**. No model-name guesses or static prices.
- New cloud APIs share the existing SSE, tool correlation and cancellation logic. Profile
  tests use upstream fixtures; adding a preset is not live certification of every model.
- Anthropic uses its OpenAI compatibility layer, not native Messages. Use a workspace-scoped
  key. This layer omits Claude thinking and ignores strict schemas; Ollama thinking controls
  are rejected explicitly rather than forwarded as ignored reasoning effort. Model listing
  uses Anthropic's version header and cursor pagination.
- Gemini tool calls may require thought signatures. Use AnyLlama's **OpenAI-compatible `/v1`**
  address and a client that retains tool-call extension fields in subsequent history. The
  OpenAI route preserves these fields. Ollama tool requests to the Gemini profile fail
  explicitly, because those signatures cannot be represented reliably there. Ordinary
  Gemini chat remains available over Ollama.
- OpenAI `max_tokens` is mapped to `max_completion_tokens`; an explicit completion limit
  takes precedence. Advanced provider-specific request parameters remain the client's job.
- Groq's streamed `x_groq.usage` is normalized for Activity. DeepSeek's cache-hit usage
  is recognized for estimates. FastFlowLM's reported decoding speed takes precedence over
  approximate stream timing. Other missing counters or prices remain unknown.

Validation: see [VALIDATION.md](VALIDATION.md). No real provider keys were used to validate
the new profiles; actual models, hardware performance and Agent behavior need live acceptance.
