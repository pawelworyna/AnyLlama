import asyncio
import copy
import json
import time
import uuid
from collections.abc import AsyncIterator

import httpx

from .codecs import Accumulator, native_request
from .metrics import catalog_prices
from .models import ProxyError, Settings


def transport_error(exc: Exception) -> ProxyError:
    if isinstance(exc, httpx.TimeoutException):
        return ProxyError(
            504, "The provider timed out. Check the connection or increase the timeout.", "upstream_timeout"
        )
    return ProxyError(
        502, "Cannot connect to the provider. Check its API URL and availability.", "upstream_connection"
    )


async def check_status(response: httpx.Response):
    if response.is_success:
        return
    status = response.status_code
    await response.aclose()
    messages = {
        400: "The provider rejected the request. Check model capabilities and parameters.",
        401: "The provider rejected the API key.",
        402: "The provider requires credits or an active subscription.",
        403: "The provider denied access to this model or API.",
        404: "The provider endpoint or model was not found.",
        429: "The provider rate limit was reached. Wait before trying again.",
    }
    # Never persist or display raw upstream bodies: they can echo prompts or credentials.
    raise ProxyError(
        status if 400 <= status <= 599 else 502,
        messages.get(status, f"The provider returned HTTP {status}."),
        "upstream_error",
    )


async def sse_objects(response: httpx.Response) -> AsyncIterator[dict]:
    data = []

    def parse(lines):
        raw = "\n".join(lines)
        if raw == "[DONE]":
            return None
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProxyError(
                502, "The provider returned malformed stream data", "invalid_upstream_stream"
            ) from exc
        if not isinstance(item, dict):
            raise ProxyError(502, "The provider returned an invalid stream object", "invalid_upstream_stream")
        if item.get("error"):
            raise ProxyError(502, "The provider reported an error during generation", "upstream_stream_error")
        return item

    async for line in response.aiter_lines():
        if not line:
            if data:
                item = parse(data)
                data = []
                if item is None:
                    return
                yield item
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        # SSE comments, event names and heartbeat frames do not contain tokens.
    if data:
        item = parse(data)
        if item is not None:
            yield item


class Adapter:
    def __init__(self, provider: dict, key: str | None, client: httpx.AsyncClient, settings: Settings):
        self.provider = provider
        self.client = client
        self.key = key
        self.timings = {}
        self.timeout = httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout)

    @property
    def headers(self):
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        if self.provider["kind"] == "openrouter":
            headers.update({"HTTP-Referer": "http://localhost", "X-Title": "AnyLlama"})
        elif self.provider["kind"] == "anthropic":
            headers["anthropic-version"] = "2026-10-21"
        return headers

    def url(self, path: str):
        return self.provider["base_url"].rstrip("/") + path

    async def get_json(self, path: str, *, method="GET", body=None, params=None, allow_list=False):
        response = await self.client.request(
            method, self.url(path), headers=self.headers, json=body, params=params, timeout=self.timeout
        )
        await check_status(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProxyError(502, "The provider returned invalid JSON", "invalid_upstream_response") from exc
        if allow_list and isinstance(data, list):
            return {"data": data}
        if not isinstance(data, dict):
            raise ProxyError(502, "The provider returned an invalid response", "invalid_upstream_response")
        return data

    async def catalog(self) -> list[dict]:
        raise NotImplementedError

    def prepare(self, data: dict) -> dict:
        return copy.deepcopy(data)

    async def open_chat(self, data: dict) -> httpx.Response:
        body = self.prepare(data)
        path = "/api/chat" if self.provider["kind"] == "ollama" else "/chat/completions"
        request = self.client.build_request(
            "POST", self.url(path), headers=self.headers, json=body, timeout=self.timeout
        )
        response = await self.client.send(request, stream=True)
        await check_status(response)
        return response

    async def chunks(self, response: httpx.Response) -> AsyncIterator[dict]:
        async for item in sse_objects(response):
            yield item

    async def completion(self, response: httpx.Response) -> dict:
        try:
            await response.aread()
            data = response.json()
            if not isinstance(data, dict) or data.get("error"):
                raise ValueError("Invalid completion")
            choice = data["choices"][0]
            if not isinstance(choice["message"], dict) or not choice.get("finish_reason"):
                raise ValueError("Incomplete completion")
            return data
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProxyError(
                502, "The provider returned an invalid or incomplete completion", "invalid_upstream_response"
            ) from exc


class OpenAIAdapter(Adapter):
    async def chunks(self, response: httpx.Response) -> AsyncIterator[dict]:
        async for item in super().chunks(response):
            # Groq's final usage frame can use its vendor envelope instead of usage.
            metadata = item.get("x_groq")
            if self.provider["kind"] == "groq" and not item.get("usage") and isinstance(metadata, dict):
                if isinstance(metadata.get("usage"), dict):
                    item["usage"] = metadata["usage"]
            yield item

    def prepare(self, data: dict) -> dict:
        result = {k: copy.deepcopy(v) for k, v in data.items() if not k.startswith("_")}
        if self.provider["kind"] == "openai" and "max_tokens" in result:
            result.setdefault("max_completion_tokens", result.pop("max_tokens"))
        # These APIs emit usage without OpenAI's optional stream_options flag.
        if result.get("stream") and self.provider["kind"] not in {
            "mistral",
            "groq",
            "together",
            "fireworks",
            "cerebras",
            "xai",
        }:
            result["stream_options"] = {**result.get("stream_options", {}), "include_usage": True}
        if "_think" in data:
            think = data["_think"]
            if self.provider["kind"] == "anthropic" and think is not None:
                raise ProxyError(
                    400,
                    "Claude thinking controls need the native Claude API; this profile uses its OpenAI compatibility layer.",
                    "unsupported_parameter",
                )
            elif self.provider["kind"] == "openrouter":
                result["reasoning"] = {"enabled": think} if isinstance(think, bool) else {"effort": think}
            elif isinstance(think, str):
                result["reasoning_effort"] = think
            elif think is not None:
                raise ProxyError(
                    400,
                    "This provider needs a named reasoning effort or its own parameters",
                    "unsupported_parameter",
                )
        return result

    async def catalog(self) -> list[dict]:
        kind = self.provider["kind"]
        params = {"limit": 1000} if kind == "anthropic" else None
        data = await self.get_json("/models", params=params, allow_list=True)
        if not isinstance(data.get("data"), list):
            raise ProxyError(502, "The provider did not return a model list", "invalid_catalog")
        items = list(data["data"])
        cursors = set()
        while kind == "anthropic" and data.get("has_more"):
            cursor = data.get("last_id")
            if not isinstance(cursor, str) or not cursor or cursor in cursors or len(cursors) >= 100:
                raise ProxyError(502, "The provider returned invalid catalog pagination", "invalid_catalog")
            cursors.add(cursor)
            data = await self.get_json("/models", params={"limit": 1000, "after_id": cursor})
            if not isinstance(data.get("data"), list):
                raise ProxyError(502, "The provider did not return a model list", "invalid_catalog")
            items.extend(data["data"])
        if any(not isinstance(item, dict) for item in items):
            raise ProxyError(502, "The provider returned invalid model metadata", "invalid_catalog")
        info = {}
        if self.provider["kind"] == "tensorx":
            try:
                metadata = await self.get_json("/model/info")
                info = {
                    m.get("model_name", m.get("id")): m.get("model_info", {})
                    for m in metadata.get("data", [])
                }
            except (ProxyError, httpx.HTTPError):
                pass  # The list remains usable if optional deployment metadata is unavailable.
        result = []
        for item in items:
            identifier = item.get("id")
            if not isinstance(identifier, str) or not identifier:
                continue
            supported = item.get("supported_parameters")
            modalities = item.get("architecture", {}).get("input_modalities")
            extra = info.get(identifier, {})
            caps = item.get("capabilities") or {}
            if not isinstance(caps, dict):
                caps = {}
            if kind == "mistral":
                if caps.get("completion_chat") is False:
                    continue
                extra = {
                    "supports_function_calling": caps.get("function_calling"),
                    "supports_vision": caps.get("vision"),
                }
            elif kind == "anthropic":
                extra = {
                    "max_input_tokens": item.get("max_input_tokens"),
                    "max_output_tokens": item.get("max_tokens"),
                }
                for source, target in (
                    ("image_input", "supports_vision"),
                    ("thinking", "supports_reasoning"),
                ):
                    if isinstance(caps.get(source), dict):
                        extra[target] = caps[source].get("supported")
            if kind == "together" and item.get("type") in {
                "embedding",
                "image",
                "audio",
                "moderation",
                "rerank",
            }:
                continue
            result.append(
                {
                    "upstream_id": identifier,
                    "name": item.get("display_name") or item.get("name") or identifier,
                    "tools": extra.get(
                        "supports_function_calling", "tools" in supported if supported is not None else None
                    ),
                    "vision": extra.get(
                        "supports_vision", "image" in modalities if modalities is not None else None
                    ),
                    "reasoning": extra.get(
                        "supports_reasoning",
                        ("reasoning" in supported or "reasoning_effort" in supported)
                        if supported is not None
                        else None,
                    ),
                    "context_length": extra.get("max_input_tokens")
                    or item.get("context_length")
                    or item.get("context_window")
                    or item.get("max_context_length"),
                    "max_output_tokens": extra.get("max_output_tokens")
                    or item.get("top_provider", {}).get("max_completion_tokens")
                    or item.get("max_completion_tokens"),
                    **catalog_prices(self.provider["kind"], item, extra),
                }
            )
        return result


class NativeNormalizer:
    def __init__(self):
        self.identifier = "chatcmpl-" + uuid.uuid4().hex
        self.created = int(time.time())
        self.next_tool = 0
        self.has_tools = False

    def chunk(self, item: dict) -> dict:
        if item.get("error"):
            raise ProxyError(502, "The provider reported an error during generation", "upstream_stream_error")
        message = item.get("message") or {}
        delta = {"role": "assistant", "content": message.get("content", "")}
        if message.get("thinking"):
            delta["reasoning"] = message["thinking"]
        if message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                index = self.next_tool
                self.next_tool += 1
                calls.append(
                    {
                        "index": index,
                        "id": call.get("id") or f"call_{self.identifier[9:]}_{index}",
                        "type": "function",
                        "function": {
                            "name": call["function"]["name"],
                            "arguments": json.dumps(call["function"].get("arguments", {})),
                        },
                    }
                )
            self.has_tools = True
            delta["tool_calls"] = calls
        finish = None
        if item.get("done"):
            finish = (
                "length"
                if item.get("done_reason") == "length"
                else ("tool_calls" if self.has_tools else "stop")
            )
        result = {
            "id": self.identifier,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": item.get("model"),
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        usage = {}
        for source, dest in (("prompt_eval_count", "prompt_tokens"), ("eval_count", "completion_tokens")):
            if item.get(source) is not None:
                usage[dest] = item[source]
        if usage:
            if "prompt_tokens" in usage and "completion_tokens" in usage:
                usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            result["usage"] = usage
        if item.get("prompt_eval_cached_count") is not None:
            result.setdefault("usage", {})["prompt_tokens_details"] = {
                "cached_tokens": item["prompt_eval_cached_count"]
            }
        return result


class OllamaAdapter(Adapter):
    def prepare(self, data: dict) -> dict:
        return native_request(data)

    async def catalog(self) -> list[dict]:
        data = await self.get_json("/api/tags")
        if not isinstance(data.get("models"), list):
            raise ProxyError(502, "The provider did not return a model list", "invalid_catalog")
        semaphore = asyncio.Semaphore(4)

        async def describe(item):
            identifier = item.get("model") or item.get("name")
            if not identifier:
                return None
            model = {
                "upstream_id": identifier,
                "name": item.get("name", identifier),
                "tools": None,
                "vision": None,
                "reasoning": None,
                "context_length": None,
                "max_output_tokens": None,
            }
            async with semaphore:
                try:
                    details = await self.get_json("/api/show", method="POST", body={"model": identifier})
                    caps = details.get("capabilities")
                    if caps is not None:
                        model.update(
                            tools="tools" in caps, vision="vision" in caps, reasoning="thinking" in caps
                        )
                    for key, value in details.get("model_info", {}).items():
                        if key.endswith(".context_length") and isinstance(value, int):
                            model["context_length"] = value
                            break
                except (ProxyError, httpx.HTTPError):
                    pass
            return model

        return [m for m in await asyncio.gather(*(describe(m) for m in data["models"])) if m]

    async def chunks(self, response: httpx.Response) -> AsyncIterator[dict]:
        normalizer = NativeNormalizer()
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("Expected an object")
                if item.get("eval_duration") is not None:
                    self.timings["eval_duration"] = item["eval_duration"]
                yield normalizer.chunk(item)
            except (ValueError, TypeError, KeyError) as exc:
                raise ProxyError(
                    502, "The provider returned malformed Ollama stream data", "invalid_upstream_stream"
                ) from exc

    async def completion(self, response: httpx.Response) -> dict:
        await response.aread()
        try:
            data = response.json()
            if data.get("eval_duration") is not None:
                self.timings["eval_duration"] = data["eval_duration"]
            accumulator = Accumulator(data.get("model", ""))
            accumulator.add(NativeNormalizer().chunk(data))
            return accumulator.completion()
        except (ValueError, TypeError, KeyError) as exc:
            raise ProxyError(
                502, "The provider returned invalid Ollama JSON", "invalid_upstream_response"
            ) from exc


def adapter_for(provider: dict, key: str | None, client: httpx.AsyncClient, settings: Settings) -> Adapter:
    cls = OllamaAdapter if provider["kind"] == "ollama" else OpenAIAdapter
    return cls(provider, key, client, settings)
