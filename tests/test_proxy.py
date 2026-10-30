import json

import httpx
import pytest
from conftest import ByteStream, chunk, completion, sse


def request(model, **extra):
    return {"model": model["id"], "messages": [{"role": "user", "content": "Hello"}], **extra}


@pytest.mark.parametrize("streaming", [False, True])
def test_visual_studio_bare_chat_completions_alias(make_client, streaming):
    seen = []

    def upstream(req):
        seen.append(json.loads(req.content))
        if streaming:
            return httpx.Response(200, stream=ByteStream(sse(chunk({"content": "VS alias works"}, "stop"))))
        return httpx.Response(200, json=completion("VS alias works"))

    client, _, model, _ = make_client(upstream)
    response = client.post("/chat/completions", json=request(model, stream=streaming))
    assert response.status_code == 200
    assert seen[0]["model"] == model["upstream_id"]
    assert "VS alias works" in response.text
    if streaming:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.text.endswith("data: [DONE]\n\n")
    else:
        assert response.json()["choices"][0]["message"]["content"] == "VS alias works"


def test_discovery_and_native_nonstream(make_client):
    seen = []

    def upstream(req):
        seen.append(json.loads(req.content))
        assert req.headers["Authorization"] == "Bearer top-secret-key"
        return httpx.Response(200, json=completion())

    client, app, model, _ = make_client(upstream)
    assert client.get("/api/tags").json()["models"][0]["name"] == model["id"]
    assert client.get("/v1/models").json()["data"][0]["id"] == model["id"]
    info = client.post("/api/show", json={"model": model["id"] + ":latest"}).json()
    assert "tools" in info["capabilities"]
    response = client.post("/api/chat", json=request(model, stream=False))
    assert response.status_code == 200
    assert response.json()["message"]["content"] == "Hello"
    assert response.json()["prompt_eval_count"] == 12
    assert seen[0]["model"] == "vendor/model"
    assert app.state.store.activity()[0]["status"] == 200


@pytest.mark.parametrize("step", [1, 3, 17, 4096])
def test_sse_to_ndjson_stream_boundaries_and_no_duplicate_text(make_client, step):
    stream = ByteStream(
        sse(
            chunk({"content": "Cześć 🦙"}),
            chunk({"content": " world"}, "stop"),
            {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}},
        ),
        step,
    )
    client, app, model, _ = make_client(lambda r: httpx.Response(200, stream=stream))
    response = client.post("/api/chat", json=request(model))
    assert response.headers["content-type"].startswith("application/x-ndjson")
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert "".join(r.get("message", {}).get("content", "") for r in rows) == "Cześć 🦙 world"
    assert rows[-1]["done"] is True
    assert rows[-1]["eval_count"] == 2
    assert app.state.store.activity()[0]["input_tokens"] == 9
    assert stream.closed


def test_parallel_streamed_tool_calls_and_followup_roundtrip(make_client):
    seen = []
    first = sse(
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "one",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"p":'},
                    },
                    {
                        "index": 1,
                        "id": "two",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"p":'},
                    },
                ]
            }
        ),
        chunk(
            {
                "tool_calls": [
                    {"index": 1, "function": {"arguments": '"b"}'}},
                    {"index": 0, "function": {"arguments": '"a"}'}},
                ]
            },
            "tool_calls",
        ),
    )

    def upstream(req):
        seen.append(json.loads(req.content))
        return (
            httpx.Response(200, stream=ByteStream(first))
            if len(seen) == 1
            else httpx.Response(200, json=completion("Both read"))
        )

    client, _, model, _ = make_client(upstream)
    rows = [json.loads(line) for line in client.post("/api/chat", json=request(model)).text.splitlines()]
    calls = [c for row in rows for c in row.get("message", {}).get("tool_calls", [])]
    assert len(calls) == 2
    assert calls[0]["function"]["arguments"] == {"p": "a"}
    messages = request(model)["messages"] + [
        {"role": "assistant", "content": "", "tool_calls": calls},
        {"role": "tool", "tool_name": "read", "content": "A"},
        {"role": "tool", "tool_name": "read", "content": "B"},
    ]
    response = client.post("/api/chat", json={"model": model["id"], "messages": messages, "stream": False})
    assert response.status_code == 200
    assert [m["tool_call_id"] for m in seen[1]["messages"] if m["role"] == "tool"] == ["one", "two"]


def test_native_ollama_to_openai_stream(make_client):
    native = [
        {"model": "model", "message": {"role": "assistant", "thinking": "Think"}, "done": False},
        {"model": "model", "message": {"role": "assistant", "content": "Answer"}, "done": False},
        {
            "model": "model",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "prompt_eval_count": 3,
            "eval_count": 2,
        },
    ]
    seen = []

    def upstream(req):
        seen.append(json.loads(req.content))
        assert req.url.path == "/api/chat"
        return httpx.Response(200, stream=ByteStream("\n".join(json.dumps(r) for r in native)))

    client, _, model, _ = make_client(upstream, kind="ollama")
    response = client.post("/v1/chat/completions", json=request(model, stream=True))
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
    assert frames[0]["choices"][0]["delta"]["reasoning"] == "Think"
    assert frames[1]["choices"][0]["delta"]["content"] == "Answer"
    assert frames[-1]["usage"]["total_tokens"] == 5
    assert response.text.endswith("data: [DONE]\n\n")


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 429, 500])
def test_upstream_errors_keep_http_status_before_stream_starts(make_client, status):
    client, app, model, _ = make_client(
        lambda r: httpx.Response(status, json={"error": "top-secret-key private prompt"})
    )
    response = client.post("/api/chat", json=request(model))
    assert response.status_code == status
    assert "top-secret-key" not in response.text
    assert app.state.store.activity()[0]["status"] == status
    assert "private prompt" not in json.dumps(app.state.store.activity())


@pytest.mark.parametrize("protocol", ["ollama", "openai"])
def test_truncated_stream_never_reports_success(make_client, protocol):
    client, app, model, _ = make_client(
        lambda r: httpx.Response(200, stream=ByteStream(sse(chunk({"content": "partial"}), done=False)))
    )
    path = "/api/chat" if protocol == "ollama" else "/v1/chat/completions"
    response = client.post(path, json=request(model, stream=True))
    assert "error" in response.text
    assert '"done": true' not in response.text
    assert "[DONE]" not in response.text
    assert app.state.store.activity()[0]["status"] == 502


def test_midstream_provider_error_is_not_success(make_client):
    client, app, model, _ = make_client(
        lambda r: httpx.Response(
            200, stream=ByteStream(sse(chunk({"content": "partial"}), {"error": {"message": "private"}}))
        )
    )
    response = client.post("/api/chat", json=request(model))
    assert "error" in response.text and '"done": true' not in response.text
    assert app.state.store.activity()[0]["error"] == "upstream_stream_error"


def test_timeout_and_pause(make_client):
    def upstream(req):
        raise httpx.ReadTimeout("private", request=req)

    client, app, model, _ = make_client(upstream)
    assert client.post("/api/chat", json=request(model)).status_code == 504
    app.state.paused = True
    assert client.post("/api/chat", json=request(model)).status_code == 503
    assert client.get("/api/tags").status_code == 200


def test_generate_and_unknown_model_and_tools(make_client):
    client, _, model, _ = make_client(tools=False)
    response = client.post("/api/generate", json={"model": model["id"], "prompt": "hello", "stream": False})
    assert response.json()["response"] == "Hello"
    assert client.post("/api/chat", json=request(model, tools=[{}])).status_code == 400
    assert (
        client.post(
            "/api/chat", json={"model": "missing", "messages": [{"role": "user", "content": "x"}]}
        ).status_code
        == 404
    )


def test_openai_nonstream_preserves_opaque_provider_state(make_client):
    result = completion()
    result["choices"][0]["message"]["reasoning_details"] = [{"type": "reasoning.encrypted", "data": "opaque"}]
    client, _, model, _ = make_client(lambda r: httpx.Response(200, json=result))
    response = client.post("/v1/chat/completions", json=request(model))
    assert response.json()["choices"][0]["message"]["reasoning_details"][0]["data"] == "opaque"
    assert client.post("/api/chat", json=request(model, stream=False)).status_code == 502
