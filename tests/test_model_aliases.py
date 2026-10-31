import json
from types import SimpleNamespace

import httpx
import pytest
from conftest import completion

from anyllama.storage import Store


@pytest.mark.parametrize("path", ["/api/chat", "/v1/chat/completions", "/chat/completions"])
def test_friendly_and_existing_ids_route_to_same_model(make_client, path):
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    client, app, model, _ = make_client(upstream)
    friendly = model["public_id"]
    assert friendly == "test-provider/vendor/model"
    assert client.post("/api/show", json={"model": friendly + ":latest"}).status_code == 200
    for identifier in (friendly, friendly + ":latest", model["id"], model["id"] + ":latest"):
        response = client.post(path, json={"model": identifier, "messages": [{"role": "user", "content": "hi"}], "stream": False})
        assert response.status_code == 200, response.text
        expected = friendly if identifier.startswith(friendly) else model["id"]
        assert response.json()["model"] == expected
    assert all(request["model"] == "vendor/model" for request in requests)
    assert app.state.store.model(friendly)["id"] == model["id"]


def test_aliases_remain_unique_and_never_reassigned(tmp_path, monkeypatch):
    path = tmp_path / "data.db"
    store = Store(path)
    data = {"name": "LM Studio", "kind": "custom", "base_url": "http://127.0.0.1", "has_key": False}
    store.save_provider(data, "123456789abc")
    store.save_provider(data, "abcdef123456")
    model = {"upstream_id": "vendor/model", "name": "Model", "enabled": True}
    first = store.add_model("123456789abc", model)
    second = store.add_model("abcdef123456", model)
    assert first["public_id"] == "lm-studio/vendor/model"
    assert second["public_id"] == "lm-studio-2/vendor/model"
    store.save_provider({**data, "name": "Renamed"}, "123456789abc")
    assert store.model(first["id"])["public_id"] == first["public_id"]
    store.delete_provider("123456789abc")
    with pytest.raises(ValueError, match="retired provider ID"):
        store.save_provider(data, "123456789abc")
    ids = iter(["123456789abc00000000000000000000", "abcd1234abcd00000000000000000000"])
    monkeypatch.setattr("anyllama.storage.uuid.uuid4", lambda: SimpleNamespace(hex=next(ids)))
    assert store.new_provider_id() == "abcd1234abcd"
    store.save_provider(data, "abcd1234abcd")
    third = store.add_model("abcd1234abcd", model)
    assert third["public_id"] == "lm-studio-3/vendor/model"
    assert store.model(first["public_id"]) is None
    store.close()
    reopened = Store(path)
    assert reopened.model(second["public_id"])["id"] == second["id"]
    assert reopened.model(third["public_id"])["id"] == third["id"]
    reopened.close()


def test_existing_database_gets_stable_aliases_without_changing_ids(tmp_path):
    path = tmp_path / "data.db"
    store = Store(path)
    data = {"name": "TensorX", "kind": "custom", "base_url": "https://provider.test", "has_key": False}
    store.save_provider(data, "abcdef123456")
    original = store.add_model("abcdef123456", {"upstream_id": "vendor/model", "name": "M", "enabled": True})
    store.db.execute("DROP TABLE provider_aliases")
    store.db.execute("PRAGMA user_version=3")
    store.db.commit()
    store.close()
    for _ in range(2):
        store = Store(path)
        assert store.model(original["id"])["public_id"] == "tensorx/vendor/model"
        assert store.model("tensorx/vendor/model")["id"] == original["id"]
        store.close()


def test_friendly_prefix_cannot_claim_legacy_id_namespace(tmp_path):
    store = Store(tmp_path / "data.db")
    data = {"name": "abcdef123456", "kind": "custom", "base_url": "https://provider.test", "has_key": False}
    store.save_provider(data, "111111111111")
    friendly_model = store.add_model(
        "111111111111", {"upstream_id": "vendor/model", "name": "M", "enabled": True}
    )
    assert friendly_model["public_id"] == "abcdef123456-provider/vendor/model"
    store.save_provider({**data, "name": "Other"}, "abcdef123456")
    legacy_model = store.add_model(
        "abcdef123456", {"upstream_id": "vendor/model", "name": "M", "enabled": True}
    )
    assert store.model(legacy_model["id"])["id"] == legacy_model["id"]
    assert store.model(friendly_model["public_id"])["id"] == friendly_model["id"]
    store.close()
