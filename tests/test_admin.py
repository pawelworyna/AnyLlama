import json

import httpx
from conftest import login


def test_session_csrf_host_and_origin_protection(make_client):
    client, _, _, _ = make_client()
    assert client.get("/admin/api/state").status_code == 401
    assert client.post("/admin/api/session", headers={"Authorization": "Bearer wrong"}).status_code == 401
    headers = login(client)
    assert client.get("/admin/api/state").status_code == 200
    assert client.post("/admin/api/pause", json={"paused": True}).status_code == 403
    assert client.post("/admin/api/pause", json={"paused": True}, headers=headers).status_code == 200
    assert client.get("/api/tags", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/tags", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/admin/api/state", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/admin").headers["content-security-policy"].find("frame-ancestors 'none'") >= 0
    assert (
        "HttpOnly"
        in client.post("/admin/api/session", headers={"Authorization": "Bearer test-bootstrap"}).headers[
            "set-cookie"
        ]
    )


def test_first_provider_workflow_and_secrets_never_returned(make_client):
    def upstream(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "vendor/new",
                        "name": "New model",
                        "context_length": 32768,
                        "supported_parameters": ["tools", "reasoning"],
                    }
                ]
            },
        )

    client, app, _, vault = make_client(upstream)
    headers = login(client)
    payload = {
        "name": "My router",
        "kind": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "new-secret",
    }
    response = client.post("/admin/api/providers", json=payload, headers=headers)
    assert response.status_code == 200
    provider_id = response.json()["id"]
    assert vault.get(provider_id) == "new-secret"
    assert "new-secret" not in response.text
    assert client.post(f"/admin/api/providers/{provider_id}/refresh", headers=headers).json()["count"] == 1
    model_id = f"{provider_id}/vendor/new"
    assert (
        client.patch(f"/admin/api/models/{model_id}", json={"enabled": True}, headers=headers).status_code
        == 200
    )
    assert model_id in [m["name"] for m in client.get("/api/tags").json()["models"]]
    export = client.get("/admin/api/export")
    assert "new-secret" not in export.text and "top-secret-key" not in export.text
    assert "api_key" not in export.text
    saved = app.state.store.model(model_id)
    assert saved["tools"] is True and saved["context_length"] == 32768
    assert client.delete(f"/admin/api/providers/{provider_id}", headers=headers).status_code == 200
    assert vault.get(provider_id) is None and app.state.store.model(model_id) is None


def test_catalog_failure_preserves_models_and_manual_overrides(make_client):
    attempts = []

    def upstream(request):
        attempts.append(1)
        if len(attempts) > 1:
            return httpx.Response(503, json={"error": "private data"})
        return httpx.Response(
            200,
            json={"data": [{"id": "vendor/model", "name": "Refreshed", "supported_parameters": ["tools"]}]},
        )

    client, app, model, _ = make_client(upstream)
    headers = login(client)
    client.patch(
        f"/admin/api/models/{model['id']}",
        headers=headers,
        json={"name": "My alias", "context_length": 50000, "vision": True},
    )
    assert client.post("/admin/api/providers/connection1/refresh", headers=headers).status_code == 200
    assert client.post("/admin/api/providers/connection1/refresh", headers=headers).status_code == 503
    saved = app.state.store.model(model["id"])
    assert saved["enabled"] and saved["name"] == "My alias" and saved["context_length"] == 50000
    assert app.state.store.provider("connection1")["error"]
    assert "private data" not in json.dumps(app.state.store.export())


def test_manual_custom_model_and_duplicate_validation(make_client):
    client, app, _, _ = make_client()
    headers = login(client)
    provider = client.post(
        "/admin/api/providers",
        headers=headers,
        json={"name": "Local", "kind": "custom", "base_url": "http://127.0.0.1:1234/v1"},
    ).json()
    assert provider["has_key"] is False
    path = f"/admin/api/providers/{provider['id']}/models"
    data = {"upstream_id": "local-model", "name": "Local model", "enabled": True, "tools": None}
    assert client.post(path, headers=headers, json=data).status_code == 200
    assert client.post(path, headers=headers, json=data).status_code == 409
    assert app.state.store.model(f"{provider['id']}/local-model")["tools"] is None
    bad = {
        "name": "Bad",
        "kind": "custom",
        "base_url": "https://user:secret@example.org",
        "api_key": "sensitive-input",
    }
    response = client.post("/admin/api/providers", headers=headers, json=bad)
    assert (
        response.status_code == 422
        and "sensitive-input" not in response.text
        and "secret@" not in response.text
    )


def test_model_names_are_unique_per_connection(make_client):
    _, app, model, _ = make_client()
    store = app.state.store
    store.save_provider(
        {"name": "Other", "kind": "custom", "base_url": "http://127.0.0.1:1234", "has_key": False},
        "connection2",
    )
    other = store.add_model(
        "connection2", {"upstream_id": "vendor/model", "name": "Same model", "enabled": True}
    )
    assert model["id"] != other["id"]
    assert len(store.models(True)) == 2


def test_key_replacement_and_endpoint_immutability(make_client):
    client, _, _, vault = make_client()
    headers = login(client)
    data = {
        "name": "Renamed",
        "kind": "openrouter",
        "base_url": "https://provider.test",
        "api_key": "replacement",
    }
    assert client.put("/admin/api/providers/connection1", headers=headers, json=data).status_code == 200
    assert vault.get("connection1") == "replacement"
    del data["api_key"]
    assert client.put("/admin/api/providers/connection1", headers=headers, json=data).status_code == 200
    assert vault.get("connection1") == "replacement"
    data["base_url"] = "https://other.test"
    assert client.put("/admin/api/providers/connection1", headers=headers, json=data).status_code == 400


def test_settings_port_requires_restart_and_timeouts_apply(make_client):
    client, _, _, _ = make_client()
    headers = login(client)
    data = {"port": 11436, "theme": "dark", "connect_timeout": 20, "read_timeout": 300, "autostart": False}
    assert client.put("/admin/api/settings", headers=headers, json=data).json()["restart_required"]
    state = client.get("/admin/api/state").json()
    assert state["settings"]["port"] == 11436 and state["endpoint"].endswith(":11434")
    assert client.get("/admin/static/app.js").status_code == 200
    assert client.get("/admin/static/htmx.min.js").status_code == 200
