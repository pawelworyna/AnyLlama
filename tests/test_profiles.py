"""Profile acceptance against controlled upstream contracts, never real accounts."""

import json

import httpx
import pytest
from conftest import ByteStream, chunk, completion, login, sse

from anyllama.models import PRESETS, ProxyError, Settings
from anyllama.providers import adapter_for

NEW_PROFILES = (
    "openai",
    "anthropic",
    "gemini",
    "deepseek",
    "xai",
    "mistral",
    "groq",
    "together",
    "fireworks",
    "cerebras",
)


LOCAL_PROFILES = ("lmstudio", "fastflowlm", "jan", "lemonade", "omlx")


@pytest.mark.parametrize("kind", NEW_PROFILES + LOCAL_PROFILES)
def test_profile_catalog_generation_stream_and_tool_followup(make_client, kind):
    profile = PRESETS[kind]
    seen = []
    tool_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "tool-1", "type": "function", "function": {"name": "read", "arguments": '{"path":"a"}'}}
        ],
    }
    if kind == "gemini":
        tool_message["tool_calls"][0]["extra_content"] = {"google": {"thought_signature": "opaque-replay"}}

    def upstream(req):
        assert req.headers["Authorization"] == "Bearer profile-fixture-key"
        assert str(req.url).startswith(profile["base_url"] + "/")
        if kind == "anthropic":
            assert req.headers["anthropic-version"] == "2026-10-21"
        if req.method == "GET":
            assert req.url.path.endswith("/models")
            model = {"id": "vendor/shared-model"}
            if kind == "anthropic":
                assert req.url.params["limit"] == "1000"
                model.update(
                    display_name="Claude fixture",
                    max_input_tokens=200000,
                    max_tokens=8192,
                    capabilities={"image_input": {"supported": True}, "thinking": {"supported": True}},
                )
            if kind == "mistral":
                model.update(
                    capabilities={"completion_chat": True, "function_calling": True, "vision": False},
                    max_context_length=32000,
                )
            if kind == "groq":
                model.update(context_window=64000, max_completion_tokens=4096)
            return httpx.Response(200, json=[model] if kind == "together" else {"data": [model]})
        body = json.loads(req.content)
        seen.append(body)
        assert req.url.path.endswith("/chat/completions")
        assert body["model"] == "vendor/shared-model"
        if body["stream"]:
            return httpx.Response(
                200,
                stream=ByteStream(
                    sse(
                        chunk({"content": "Hello"}),
                        chunk({}, "stop", {"prompt_tokens": 12, "completion_tokens": 4}),
                    )
                ),
            )
        if body.get("tools") and body["messages"][-1]["role"] != "tool":
            data = completion()
            data["choices"][0] = {"index": 0, "message": tool_message, "finish_reason": "tool_calls"}
            return httpx.Response(200, json=data)
        return httpx.Response(200, json=completion("Done"))

    client, app, _, vault = make_client(upstream)
    headers = login(client)
    provider = client.post(
        "/admin/api/providers",
        headers=headers,
        json={
            "name": profile["name"],
            "kind": kind,
            "base_url": profile["base_url"],
            "api_key": "profile-fixture-key",
        },
    ).json()
    assert vault.get(provider["id"]) == "profile-fixture-key"
    client.post(f"/admin/api/providers/{provider['id']}/refresh", headers=headers).raise_for_status()
    model_id = f"{provider['id']}/vendor/shared-model"
    client.patch(f"/admin/api/models/{model_id}", headers=headers, json={"enabled": True}).raise_for_status()
    model = app.state.store.model(model_id)
    if kind == "mistral":
        assert model["tools"] is True and model["vision"] is False and model["context_length"] == 32000
    elif kind == "anthropic":
        assert model["vision"] is True and model["name"] == "Claude fixture"
        assert model["max_output_tokens"] == 8192
        assert model["tools"] is None
    elif kind == "groq":
        assert model["context_length"] == 64000 and model["max_output_tokens"] == 4096
    else:
        assert model["tools"] is None and model["context_length"] is None
    assert model["input_price_per_million"] is None
    assert model["public_id"] in [item["name"] for item in client.get("/api/tags").json()["models"]]
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Fixture prompt"}],
        "stream": False,
    }
    response = client.post("/api/chat", json={**payload, "options": {"num_predict": 17}})
    assert response.json()["message"]["content"] == "Done"
    if kind == "openai":
        assert seen[-1]["max_completion_tokens"] == 17 and "max_tokens" not in seen[-1]
    response = client.post("/v1/chat/completions", json={**payload, "stream": True})
    assert response.text.endswith("data: [DONE]\n\n") and "Hello" in response.text
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
    response = client.post("/v1/chat/completions", json={**payload, "tools": tools})
    message = response.json()["choices"][0]["message"]
    assert message == tool_message
    followup = {
        **payload,
        "tools": tools,
        "messages": payload["messages"]
        + [message, {"role": "tool", "tool_call_id": "tool-1", "content": "File result"}],
    }
    assert (
        client.post("/v1/chat/completions", json=followup).json()["choices"][0]["message"]["content"]
        == "Done"
    )
    assert seen[-1]["messages"][-2] == tool_message
    assert app.state.store.activity()[0]["input_tokens"] == 12
    assert "profile-fixture-key" not in client.get("/admin/api/state").text


async def test_anthropic_catalog_pagination_keeps_names_and_cursor():
    def upstream(req):
        cursor = req.url.params.get("after_id")
        if cursor is None:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "one", "display_name": "First"}],
                    "has_more": True,
                    "last_id": "cursor /&value",
                },
            )
        assert cursor == "cursor /&value"
        return httpx.Response(
            200, json={"data": [{"id": "two", "display_name": "Second"}], "has_more": False}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        models = await adapter_for(
            {"kind": "anthropic", "base_url": PRESETS["anthropic"]["base_url"]}, "fixture", client, Settings()
        ).catalog()
    assert [(m["upstream_id"], m["name"]) for m in models] == [("one", "First"), ("two", "Second")]


async def test_anthropic_repeated_cursor_is_an_error():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"data": [], "has_more": True, "last_id": "same"})
        )
    ) as client:
        adapter = adapter_for(
            {"kind": "anthropic", "base_url": PRESETS["anthropic"]["base_url"]}, "fixture", client, Settings()
        )
        with pytest.raises(ProxyError, match="pagination"):
            await adapter.catalog()


@pytest.mark.parametrize("kind", ["mistral", "together"])
async def test_nonchat_models_are_filtered_using_advertised_metadata(kind):
    items = [
        {"id": "chat"},
        {"id": "embedding", "type": "embedding", "capabilities": {"completion_chat": False}},
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"data": items}))
    ) as client:
        models = await adapter_for(
            {"kind": kind, "base_url": PRESETS[kind]["base_url"]}, "fixture", client, Settings()
        ).catalog()
        assert [m["upstream_id"] for m in models] == ["chat"]


def test_gemini_ollama_tools_fail_before_upstream_instead_of_losing_signatures(make_client):
    seen = []
    client, _, model, _ = make_client(lambda req: seen.append(req), kind="gemini")
    response = client.post(
        "/api/chat",
        json={
            "model": model["id"],
            "messages": [{"role": "user", "content": "Read a file"}],
            "tools": [{"type": "function", "function": {"name": "read"}}],
        },
    )
    assert response.status_code == 400 and "/v1" in response.json()["error"]
    assert not seen


def test_claude_thinking_control_is_not_silently_ignored(make_client):
    seen = []
    client, _, model, _ = make_client(lambda req: seen.append(req), kind="anthropic")
    response = client.post(
        "/api/chat",
        json={"model": model["id"], "think": "high", "messages": [{"role": "user", "content": "Hello"}]},
    )
    assert response.status_code == 400 and "thinking" in response.json()["error"]
    assert not seen


def test_unknown_provider_profile_is_rejected_without_echoing_key(make_client):
    client, _, _, _ = make_client()
    response = client.post(
        "/admin/api/providers",
        headers=login(client),
        json={
            "name": "Bad",
            "kind": "unknown",
            "base_url": "https://example.test",
            "api_key": "sensitive-fixture",
        },
    )
    assert response.status_code == 422 and "sensitive-fixture" not in response.text


@pytest.mark.parametrize("kind", LOCAL_PROFILES)
def test_local_profile_without_key_and_optional_key_removal(make_client, kind):
    seen = []

    def upstream(req):
        seen.append(req)
        assert "authorization" not in req.headers
        if req.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "local-model"}]})
        return httpx.Response(200, json=completion("Local OK"))

    client, app, _, vault = make_client(upstream)
    headers = login(client)
    body = {"name": PRESETS[kind]["name"], "kind": kind, "base_url": PRESETS[kind]["base_url"]}
    saved = client.post("/admin/api/providers", headers=headers, json=body)
    saved.raise_for_status()
    provider = saved.json()
    assert not provider["has_key"]
    client.post(f"/admin/api/providers/{provider['id']}/refresh", headers=headers).raise_for_status()
    model_id = f"{provider['id']}/local-model"
    client.patch(f"/admin/api/models/{model_id}", headers=headers, json={"enabled": True}).raise_for_status()
    result = client.post(
        "/api/chat",
        json={"model": model_id, "stream": False, "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert result.json()["message"]["content"] == "Local OK"
    path = f"/admin/api/providers/{provider['id']}"
    client.put(path, headers=headers, json={**body, "api_key": "optional-fixture-key"}).raise_for_status()
    assert vault.get(provider["id"]) == "optional-fixture-key"
    client.put(path, headers=headers, json={**body, "api_key": ""}).raise_for_status()
    assert vault.get(provider["id"]) is None and not app.state.store.provider(provider["id"])["has_key"]


def test_groq_usage_frame_is_normalized_for_activity(make_client):
    data = sse(
        chunk({"content": "Answer"}, "stop"),
        {
            "choices": [],
            "x_groq": {"usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}},
        },
    )
    client, app, model, _ = make_client(lambda req: httpx.Response(200, stream=ByteStream(data)), kind="groq")
    result = client.post(
        "/api/chat", json={"model": model["id"], "messages": [{"role": "user", "content": "Hi"}]}
    )
    assert json.loads(result.text.splitlines()[-1])["eval_count"] == 10
    assert app.state.store.activity()[0]["input_tokens"] == 30


def test_cloud_profile_still_requires_key(make_client):
    client, _, _, _ = make_client()
    headers = login(client)
    for kind in NEW_PROFILES:
        response = client.post(
            "/admin/api/providers",
            headers=headers,
            json={"name": kind, "kind": kind, "base_url": PRESETS[kind]["base_url"]},
        )
        assert response.status_code == 400 and response.json()["error"]["code"] == "missing_key"
