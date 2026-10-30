import json

import httpx
import pytest

from anyllama.models import Settings
from anyllama.providers import adapter_for


async def test_tensorx_model_metadata_and_unknown_fields():
    def upstream(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "tool-model"}, {"id": "unknown"}]})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "model_name": "tool-model",
                        "model_info": {
                            "supports_function_calling": True,
                            "supports_vision": False,
                            "supports_reasoning": True,
                            "max_input_tokens": 64000,
                            "max_output_tokens": 8192,
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter = adapter_for(
            {"kind": "tensorx", "base_url": "https://api.tensorx.ai/v1"}, "key", client, Settings()
        )
        models = await adapter.catalog()
        assert models[0]["tools"] is True and models[0]["vision"] is False
        assert models[0]["context_length"] == 64000
        assert models[1]["tools"] is None


async def test_ollama_catalog_uses_native_metadata():
    def upstream(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "cloud-model"}]})
        assert json.loads(request.content)["model"] == "cloud-model"
        return httpx.Response(
            200,
            json={
                "capabilities": ["completion", "tools", "thinking"],
                "model_info": {"test.context_length": 128000},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        adapter = adapter_for({"kind": "ollama", "base_url": "https://ollama.com"}, "key", client, Settings())
        models = await adapter.catalog()
        assert models[0]["tools"] is True and models[0]["vision"] is False
        assert models[0]["context_length"] == 128000


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"models":null}'])
async def test_invalid_catalog_is_not_an_empty_success(body):
    from anyllama.models import ProxyError

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    ) as client:
        adapter = adapter_for({"kind": "ollama", "base_url": "https://ollama.com"}, "key", client, Settings())
        with pytest.raises(ProxyError):
            await adapter.catalog()
