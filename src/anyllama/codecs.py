"""Wire conversion through a Chat Completions-shaped intermediate representation.

Extra message fields survive OpenAI → OpenAI. Unsupported signed/encrypted replay
fails rather than silently dropping the data needed for a subsequent tool turn.
"""

import base64
import binascii
import copy
import hashlib
import json
import time
import uuid

from .models import ProxyError
from .storage import utcnow


def argument_object(value, status=502):
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise ProxyError(
                status, "Tool arguments must contain valid JSON", "invalid_tool_arguments"
            ) from exc
    if not isinstance(value, dict):
        raise ProxyError(status, "Tool arguments must be a JSON object", "invalid_tool_arguments")
    return value


def image_url(image: str) -> str:
    if not isinstance(image, str):
        raise ProxyError(400, "Images must be base64 strings", "invalid_image")
    try:
        header = base64.b64decode(image[:32])
    except (ValueError, binascii.Error) as exc:
        raise ProxyError(400, "Invalid base64 image", "invalid_image") from exc
    mime = "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif header.startswith(b"GIF8"):
        mime = "image/gif"
    elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        mime = "image/webp"
    return f"data:{mime};base64,{image}"


def reasoning_text(message: dict, status=502) -> str:
    parts = message.get("reasoning_details") or []
    for part in parts:
        if part.get("type") not in {"reasoning.text", "reasoning.summary"} or part.get("signature"):
            raise ProxyError(
                status,
                "This reasoning replay cannot be translated to Ollama. Use the OpenAI-compatible endpoint.",
                "unsupported_reasoning_replay",
            )
    return (
        message.get("reasoning")
        or message.get("reasoning_content")
        or "".join(part.get("text") or part.get("summary") or "" for part in parts)
    )


def call_id(position: int, index: int, name: str, arguments) -> str:
    seed = json.dumps([position, index, name, argument_object(arguments)], sort_keys=True)
    return "call_" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def ollama_messages(messages: list[dict]) -> list[dict]:
    result, pending = [], []
    for position, original in enumerate(messages):
        msg = copy.deepcopy(original)
        role = msg.get("role")
        if role not in {"system", "user", "assistant", "tool", "developer"}:
            raise ProxyError(400, "Unsupported message role", "invalid_request")
        if "thinking" in msg:
            msg["reasoning"] = msg.pop("thinking")
        images = msg.pop("images", [])
        if images:
            text = msg.get("content", "")
            msg["content"] = ([{"type": "text", "text": text}] if text else []) + [
                {"type": "image_url", "image_url": {"url": image_url(img)}} for img in images
            ]
        if role == "assistant" and msg.get("tool_calls"):
            if pending:
                raise ProxyError(
                    400, "Tool results are missing from the conversation", "invalid_tool_history"
                )
            calls = []
            for index, item in enumerate(msg["tool_calls"]):
                function = item["function"]
                args = argument_object(function.get("arguments", {}), status=400)
                name = function["name"]
                identifier = item.get("id") or call_id(position, index, name, args)
                calls.append(
                    {
                        "id": identifier,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                )
                pending.append((identifier, name))
            msg["tool_calls"] = calls
        if role == "tool":
            identifier = msg.get("tool_call_id")
            name = msg.pop("tool_name", None) or msg.pop("name", None)
            candidates = [
                p for p in pending if (p[0] == identifier if identifier else not name or p[1] == name)
            ]
            if not candidates:
                raise ProxyError(400, "Cannot match a tool result to its call", "invalid_tool_history")
            matched = candidates[0]
            msg["tool_call_id"] = matched[0]
            pending.remove(matched)
        result.append(msg)
    if pending:
        raise ProxyError(400, "Tool results are missing from the conversation", "invalid_tool_history")
    return result


def decode_request(body: dict, protocol: str, generate=False) -> dict:
    if not isinstance(body.get("model"), str) or not body["model"]:
        raise ProxyError(400, "A model is required", "invalid_request")
    if "stream" in body and not isinstance(body["stream"], bool):
        raise ProxyError(400, "stream must be a boolean", "invalid_request")
    if protocol == "openai":
        data = copy.deepcopy(body)
        data.setdefault("stream", False)
    else:
        data = {"model": body["model"], "stream": body.get("stream", True)}
        messages = body.get("messages", [])
        if generate:
            if body.get("raw") or body.get("template") or body.get("context"):
                raise ProxyError(
                    400, "Raw templates and token contexts are not supported", "unsupported_parameter"
                )
            messages = [{"role": "system", "content": body["system"]}] if body.get("system") else []
            messages.append(
                {"role": "user", "content": body.get("prompt", ""), "images": body.get("images", [])}
            )
        if not isinstance(messages, list):
            raise ProxyError(400, "messages must be an array", "invalid_request")
        data["messages"] = ollama_messages(messages)
        for key in ("tools", "tool_choice"):
            if key in body:
                data[key] = body[key]
        options = body.get("options") or {}
        for source, dest in {
            "temperature": "temperature",
            "top_p": "top_p",
            "seed": "seed",
            "stop": "stop",
            "num_predict": "max_tokens",
        }.items():
            if source in options:
                data[dest] = options[source]
        if "think" in body:
            if body["think"] is not None and not isinstance(body["think"], (bool, str)):
                raise ProxyError(400, "think must be a boolean or named reasoning effort", "invalid_request")
            data["_think"] = body["think"]
        if body.get("format"):
            fmt = body["format"]
            data["response_format"] = (
                {"type": "json_object"}
                if fmt == "json"
                else {"type": "json_schema", "json_schema": {"name": "response", "schema": fmt}}
            )
    if not isinstance(data.get("messages"), list) or not data["messages"]:
        raise ProxyError(400, "A nonempty messages array is required", "invalid_request")
    if data.get("n", 1) != 1:
        raise ProxyError(400, "Only one completion per request is supported", "unsupported_parameter")
    for msg in data["messages"]:
        if not isinstance(msg, dict) or msg.get("role") not in {
            "system",
            "user",
            "assistant",
            "tool",
            "developer",
        }:
            raise ProxyError(400, "Each message must have a supported role", "invalid_request")
        message_content = msg.get("content")
        if message_content is not None and not isinstance(message_content, (str, list)):
            raise ProxyError(400, "Message content must be text or an array of parts", "invalid_request")
        if isinstance(message_content, list) and any(not isinstance(part, dict) for part in message_content):
            raise ProxyError(400, "Message parts must be objects", "invalid_request")
    return data


def native_request(data: dict) -> dict:
    messages = []
    for original in data["messages"]:
        msg = copy.deepcopy(original)
        thinking = reasoning_text(msg, status=400)
        for field in ("reasoning", "reasoning_content", "reasoning_details"):
            msg.pop(field, None)
        if thinking:
            msg["thinking"] = thinking
        content = msg.get("content")
        if isinstance(content, list):
            texts, images = [], []
            for part in content:
                if part.get("type") == "text":
                    texts.append(part["text"])
                elif part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if not url.startswith("data:image/") or ";base64," not in url:
                        raise ProxyError(
                            400, "Ollama image inputs require base64 data URLs", "unsupported_image"
                        )
                    images.append(url.split(";base64,", 1)[1])
                else:
                    raise ProxyError(400, "Unsupported Ollama message content", "unsupported_content")
            msg["content"] = "\n".join(texts)
            if images:
                msg["images"] = images
        if msg.get("tool_calls"):
            for tool in msg["tool_calls"]:
                tool["function"]["arguments"] = argument_object(tool["function"].get("arguments", {}))
        if msg.get("role") == "tool":
            # Current Ollama accepts tool_call_id; tool_name also supports older native backends.
            for previous in reversed(messages):
                matched = next(
                    (t for t in previous.get("tool_calls", []) if t.get("id") == msg.get("tool_call_id")),
                    None,
                )
                if matched:
                    msg["tool_name"] = matched["function"]["name"]
                    break
        messages.append(msg)
    result = {"model": data["model"], "messages": messages, "stream": data["stream"]}
    for key in ("tools", "tool_choice"):
        if key in data:
            result[key] = data[key]
    options = {}
    for source, dest in {
        "temperature": "temperature",
        "top_p": "top_p",
        "seed": "seed",
        "stop": "stop",
        "max_tokens": "num_predict",
        "max_completion_tokens": "num_predict",
    }.items():
        if source in data:
            options[dest] = data[source]
    if options:
        result["options"] = options
    if "_think" in data:
        result["think"] = data["_think"]
    elif "reasoning_effort" in data:
        result["think"] = data["reasoning_effort"]
    fmt = data.get("response_format", {})
    if fmt:
        result["format"] = "json" if fmt["type"] == "json_object" else fmt["json_schema"]["schema"]
    return result


class Accumulator:
    def __init__(self, model: str, retain_content=True):
        self.model = model
        self.retain_content = retain_content
        self.identifier = "chatcmpl-" + uuid.uuid4().hex
        self.created = int(time.time())
        self.content = ""
        self.reasoning = ""
        self.details = []
        self.tools: dict[int, dict] = {}
        self.finish = None
        self.usage: dict = {}

    def add(self, chunk: dict):
        self.identifier = chunk.get("id", self.identifier)
        self.created = chunk.get("created", self.created)
        self.usage.update(chunk.get("usage") or {})
        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}
            if self.retain_content:
                self.content += delta.get("content") or ""
                self.reasoning += delta.get("reasoning") or delta.get("reasoning_content") or ""
                self.details.extend(delta.get("reasoning_details") or [])
            for item in delta.get("tool_calls") or []:
                index = item.get("index", 0)
                target = self.tools.setdefault(
                    index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                if item.get("id"):
                    target["id"] = item["id"]
                fn = item.get("function", {})
                target["function"]["name"] += fn.get("name") or ""
                args = fn.get("arguments") or ""
                target["function"]["arguments"] += json.dumps(args) if isinstance(args, dict) else args
            self.finish = choice.get("finish_reason") or self.finish

    def completion(self) -> dict:
        if not self.finish:
            raise ProxyError(502, "The provider stream ended before completion", "incomplete_stream")
        message = {"role": "assistant", "content": self.content}
        if self.reasoning:
            message["reasoning"] = self.reasoning
        if self.details:
            message["reasoning_details"] = self.details
        if self.tools:
            for tool in self.tools.values():
                argument_object(tool["function"]["arguments"])
            message["tool_calls"] = [self.tools[k] for k in sorted(self.tools)]
        return {
            "id": self.identifier,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "message": message, "finish_reason": self.finish}],
            "usage": self.usage,
        }


def native_message(message: dict) -> dict:
    result = {"role": "assistant", "content": message.get("content") or ""}
    reasoning = reasoning_text(message)
    if reasoning:
        result["thinking"] = reasoning
    if message.get("tool_calls"):
        result["tool_calls"] = [
            {
                "id": item.get("id"),
                "function": {
                    "name": item["function"]["name"],
                    "arguments": argument_object(item["function"].get("arguments", {})),
                },
            }
            for item in message["tool_calls"]
        ]
    return result


def native_result(completion: dict, model: str, duration_ns: int, generate=False) -> dict:
    choice = completion["choices"][0]
    usage = completion.get("usage") or {}
    message = native_message(choice["message"])
    result = {
        "model": model,
        "created_at": utcnow(),
        "done": True,
        "done_reason": "length" if choice["finish_reason"] == "length" else "stop",
        "total_duration": duration_ns,
    }
    for source, dest in (("prompt_tokens", "prompt_eval_count"), ("completion_tokens", "eval_count")):
        if usage.get(source) is not None:
            result[dest] = usage[source]
    if generate:
        result["response"] = message["content"]
        if "thinking" in message:
            result["thinking"] = message["thinking"]
    else:
        result["message"] = message
    return result


def native_chunk(chunk: dict, model: str, generate=False) -> dict | None:
    choices = chunk.get("choices") or []
    delta = choices[0].get("delta", {}) if choices else {}
    # Arguments are emitted separately once their JSON is complete.
    message = native_message({k: v for k, v in delta.items() if k != "tool_calls"})
    if not message["content"] and not message.get("thinking"):
        return None
    result = {"model": model, "created_at": utcnow(), "done": False}
    if generate:
        result["response"] = message["content"]
        if "thinking" in message:
            result["thinking"] = message["thinking"]
    else:
        result["message"] = message
    return result
