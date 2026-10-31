import os
import stat

import pytest

from anyllama.desktop import InstanceLock, default_data_dir
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_private_instance_metadata_on_posix(tmp_path):
    directory = tmp_path / "private"
    instance = InstanceLock(directory)
    try:
        assert instance.acquire()
        instance.publish("http://127.0.0.1:1234/admin/#token=secret")
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(instance.path.stat().st_mode) == 0o600
    finally:
        instance.close()


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


def test_platform_data_paths(monkeypatch, tmp_path):
    import anyllama.desktop as desktop

    monkeypatch.setattr(desktop.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    assert default_data_dir() == tmp_path / "Library" / "Application Support" / "AnyLlama"
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert default_data_dir() == tmp_path / "xdg" / "AnyLlama"
