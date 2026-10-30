from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

PRESETS = {
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "mark": "↗"},
    "ollama": {"name": "Ollama Cloud", "base_url": "https://ollama.com", "mark": "o"},
    "tensorx": {"name": "TensorX", "base_url": "https://api.tensorx.ai/v1", "mark": "Tx"},
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "mark": "O",
        "note": "Choose a Chat Completions model. Responses-only models are not supported. Set missing capabilities and prices in Models → Configure.",
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com/v1",
        "mark": "A",
        "note": "OpenAI compatibility mode. Use a workspace-scoped API key. Claude thinking is not returned and strict tool schemas are not enforced by this API.",
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "mark": "G",
        "note": "OpenAI compatibility mode. For Agent/tools, use AnyLlama’s /v1 endpoint with a client that preserves Gemini thought signatures. Ollama tool requests are rejected to protect conversation history.",
    },
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "mark": "D"},
    "xai": {"name": "xAI (Grok)", "base_url": "https://api.x.ai/v1", "mark": "x"},
    "mistral": {"name": "Mistral AI", "base_url": "https://api.mistral.ai/v1", "mark": "M"},
    "groq": {"name": "Groq", "base_url": "https://api.groq.com/openai/v1", "mark": "g"},
    "together": {"name": "Together AI", "base_url": "https://api.together.ai/v1", "mark": "T"},
    "fireworks": {
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "mark": "F",
        "note": "Choose an available chat model or add its full accounts/.../models/... ID manually if catalog discovery is unavailable for your account.",
    },
    "cerebras": {"name": "Cerebras", "base_url": "https://api.cerebras.ai/v1", "mark": "C"},
    "lmstudio": {
        "name": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "mark": "LM",
        "requires_key": False,
        "note": "Start LM Studio’s local API server and load a chat model. Enter a key only if server authentication is enabled. Agent tools depend on the selected model.",
    },
    "fastflowlm": {
        "name": "FastFlowLM",
        "base_url": "http://127.0.0.1:52625/v1",
        "mark": "FL",
        "requires_key": False,
        "note": "Start FastFlowLM in server mode with a chat model. Tool calling depends on the model and server version; configure missing capabilities in Models.",
    },
    "jan": {
        "name": "Jan",
        "base_url": "http://127.0.0.1:1337/v1",
        "mark": "J",
        "requires_key": False,
        "note": "Start Jan’s Local API Server. Enter its API key if configured. Keep Execute Tools on Server disabled so Visual Studio executes Agent tools.",
    },
    "lemonade": {
        "name": "Lemonade",
        "base_url": "http://127.0.0.1:13305/v1",
        "mark": "L",
        "requires_key": False,
        "note": "Start Lemonade and download a chat model. This is the current server address; older versions may use another port or /api/v1 prefix. Enter a key if server authentication is enabled.",
    },
    "omlx": {
        "name": "oMLX",
        "base_url": "http://127.0.0.1:8000/v1",
        "mark": "MLX",
        "requires_key": False,
        "note": "oMLX runs on Apple Silicon/macOS. For this Windows proxy, use an existing local tunnel to your Mac or enter its HTTPS API address. Enter the server API key if required.",
    },
    "custom": {
        "name": "Custom OpenAI-compatible",
        "base_url": "http://127.0.0.1:1234/v1",
        "mark": "<>",
        "requires_key": False,
    },
}


class ProviderInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str
    base_url: str
    api_key: str | None = Field(default=None, max_length=4096)

    @field_validator("kind")
    @classmethod
    def known_provider(cls, value: str) -> str:
        if value not in PRESETS:
            raise ValueError("Choose a supported provider profile")
        return value

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a connection name")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def clean_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("Enter an absolute HTTP or HTTPS API URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("API URLs cannot contain credentials, query parameters or fragments")
        try:
            parts.port
        except ValueError as exc:
            raise ValueError("Invalid API port") from exc
        if parts.scheme == "http":
            loopback = parts.hostname.lower() == "localhost"
            try:
                loopback = loopback or ip_address(parts.hostname).is_loopback
            except ValueError:
                pass
            if not loopback:
                raise ValueError("Remote API endpoints require HTTPS; HTTP is allowed only on loopback")
        return value.strip().rstrip("/")


class ModelInput(BaseModel):
    upstream_id: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=300)
    enabled: bool = False
    tools: bool | None = None
    vision: bool | None = None
    reasoning: bool | None = None
    context_length: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cached_input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_write_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ModelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    enabled: bool | None = None
    tools: bool | None = None
    vision: bool | None = None
    reasoning: bool | None = None
    context_length: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cached_input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_write_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Settings(BaseModel):
    port: int = Field(default=11434, ge=1024, le=65535)
    connect_timeout: float = Field(default=10, ge=1, le=120)
    read_timeout: float = Field(default=180, ge=5, le=3600)
    theme: Literal["system", "light", "dark"] = "system"
    autostart: bool = False
    check_updates: bool = True


class ProxyError(Exception):
    def __init__(self, status: int, message: str, code: str = "proxy_error"):
        self.status = status
        self.message = message
        self.code = code
        super().__init__(message)
