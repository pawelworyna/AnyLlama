import contextlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from anyllama.app import create_app


class MemoryVault:
    def __init__(self):
        self.keys = {}

    def get(self, identifier):
        return self.keys.get(identifier)

    def set(self, identifier, value):
        self.keys[identifier] = value

    def delete(self, identifier):
        self.keys.pop(identifier, None)


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, body, step=3):
        self.body = body.encode() if isinstance(body, str) else body
        self.step = step
        self.closed = False

    async def __aiter__(self):
        for index in range(0, len(self.body), self.step):
            yield self.body[index : index + self.step]

    async def aclose(self):
        self.closed = True


def chunk(delta=None, finish=None, usage=None):
    item = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "upstream",
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
    }
    if usage:
        item["usage"] = usage
    return item


def sse(*items, done=True):
    return (
        ": heartbeat\n\n"
        + "".join("data: " + json.dumps(item, ensure_ascii=False) + "\n\n" for item in items)
        + ("data: [DONE]\n\n" if done else "")
    )


def completion(content="Hello", tools=None):
    message = {"role": "assistant", "content": content}
    if tools:
        message["tool_calls"] = tools
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "upstream",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tools else "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }


@pytest.fixture
def make_client(tmp_path):
    with contextlib.ExitStack() as stack:
        count = 0

        def make(handler=None, kind="openrouter", tools=True, update_interval=0):
            nonlocal count
            count += 1
            vault = MemoryVault()
            handler = handler or (lambda request: httpx.Response(200, json=completion()))
            app = create_app(
                tmp_path / str(count),
                vault=vault,
                transport=httpx.MockTransport(handler),
                bootstrap_token="test-bootstrap",
                refresh_interval=0,
                update_interval=update_interval,
            )
            store = app.state.store
            provider = store.save_provider(
                {"name": "Test provider", "kind": kind, "base_url": "https://provider.test", "has_key": True},
                "connection1",
            )
            vault.set(provider["id"], "top-secret-key")
            model = store.add_model(
                provider["id"],
                {
                    "upstream_id": "vendor/model",
                    "name": "Test model",
                    "enabled": True,
                    "tools": tools,
                    "vision": True,
                },
            )
            client = stack.enter_context(TestClient(app, base_url="http://127.0.0.1"))
            return client, app, model, vault

        yield make


def login(client):
    response = client.post("/admin/api/session", headers={"Authorization": "Bearer test-bootstrap"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf"]}
