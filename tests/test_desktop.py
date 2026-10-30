from anyllama.desktop import InstanceLock
from anyllama.models import Settings
from anyllama.storage import Store


def test_instance_lock_and_recovery(tmp_path):
    first = InstanceLock(tmp_path)
    second = InstanceLock(tmp_path)
    try:
        assert first.acquire()
        first.publish("http://127.0.0.1:11435/admin/#token=test")
        assert not second.acquire()
        assert second.existing_url().endswith("#token=test")
        first.close()
        assert second.acquire()
    finally:
        if not first.file.closed:
            first.close()
        second.close()


def test_settings_and_model_choices_survive_reopening_database(tmp_path):
    path = tmp_path / "config.db"
    store = Store(path)
    store.save_settings(Settings(theme="dark", port=11436))
    store.save_provider(
        {"name": "Custom", "kind": "custom", "base_url": "http://127.0.0.1:1234/v1", "has_key": False}, "p"
    )
    store.add_model("p", {"upstream_id": "m", "name": "Model", "enabled": True})
    store.close()
    reopened = Store(path)
    try:
        assert reopened.settings().theme == "dark" and reopened.settings().port == 11436
        assert reopened.model("p/m")["enabled"]
    finally:
        reopened.close()
