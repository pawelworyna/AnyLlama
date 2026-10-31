import json

import httpx
import pytest
from conftest import MemoryVault, login
from fastapi.testclient import TestClient
from pydantic import ValidationError

from anyllama.app import create_app
from anyllama.models import ProviderInput
from anyllama.security import REQUEST_LIMIT, RequestLimitMiddleware


@pytest.mark.parametrize(
    "url", ["http://example.com/v1", "http://127.0.0.1.evil.test/v1", "http://8.8.8.8/v1", "http://169.254.169.254/v1"]
)
def test_remote_plaintext_endpoints_are_rejected(url):
    with pytest.raises(ValidationError, match="require HTTPS"):
        ProviderInput(name="Connection", kind="custom", base_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://[::1]:1234/v1",
        "http://192.168.0.42:1234/v1",
        "http://10.0.0.1:1234/v1",
        "http://172.16.0.1:1234/v1",
        "http://[fd00::1]:1234/v1",
        "https://example.com/v1",
    ],
)
def test_loopback_http_and_remote_https_remain_supported(url):
    assert ProviderInput(name="Connection", kind="custom", base_url=url).base_url == url


def test_lm_studio_private_ip_can_save_and_refresh(make_client):
    contacted = []

    def upstream(request):
        contacted.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    client, _, _, _ = make_client(upstream)
    headers = login(client)
    response = client.post(
        "/admin/api/providers", headers=headers,
        json={"name": "LM Studio", "kind": "lmstudio", "base_url": "http://192.168.0.42:1234/v1"},
    )
    assert response.status_code == 200, response.text
    provider_id = response.json()["id"]
    assert client.post(f"/admin/api/providers/{provider_id}/refresh", headers=headers).status_code == 200
    assert "http://192.168.0.42:1234/v1/models" in contacted


def test_declared_request_size_is_rejected_before_parsing(make_client):
    client, _, _, _ = make_client()
    result = client.post("/api/chat", content=b"{}", headers={"Content-Length": str(REQUEST_LIMIT + 1)})
    assert result.status_code == 413 and "error" in result.json()


def test_legacy_plaintext_remote_configuration_is_blocked_at_runtime(make_client):
    contacted = []
    client, app, model, _ = make_client(lambda request: contacted.append(request))
    provider = app.state.store.provider("connection1")
    provider["base_url"] = "http://remote.example/v1"
    app.state.store.save_provider(provider, "connection1")
    response = client.post(
        "/chat/completions",
        json={"model": model["id"], "messages": [{"role": "user", "content": "Private prompt"}]},
    )
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_provider_url"
    assert contacted == []


@pytest.mark.parametrize("path", ["/api/chat", "/admin/api/providers"])
def test_chunked_size_limit_also_covers_framework_parsed_admin_bodies(tmp_path, path):
    app = create_app(
        tmp_path, vault=MemoryVault(), bootstrap_token="test-bootstrap", refresh_interval=0, update_interval=0
    )
    app.add_middleware(RequestLimitMiddleware, limit=100)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = login(client)

        def chunks():
            yield b'{"extra":"'
            yield b"x" * 101
            yield b'"}'

        response = client.post(path, content=chunks(), headers=headers)
        assert response.status_code == 413
        assert "error" in response.json()


@pytest.mark.parametrize("model", [123, [], {}, None])
def test_malformed_model_reference_returns_client_error(make_client, model):
    client, _, _, _ = make_client()
    assert client.post("/api/show", json={"model": model}).status_code == 400


def test_untrusted_catalog_limits_are_validated_before_persistence(make_client):
    def provider(request):
        return httpx.Response(
            200, json={"data": [{"id": "bad", "context_length": '" autofocus onfocus="bad'}]}
        )

    client, app, model, _ = make_client(provider)
    response = client.post("/admin/api/providers/connection1/refresh", headers=login(client))
    assert response.status_code == 502
    assert app.state.store.model(model["id"])
    assert app.state.store.model("connection1/bad") is None
    assert "autofocus" not in json.dumps(app.state.store.export())
