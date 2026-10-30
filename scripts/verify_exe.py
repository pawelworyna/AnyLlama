"""Smoke-test the frozen Windows app with isolated data and a temporary dummy key."""

import argparse
import contextlib
import json
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from anyllama import __version__
from anyllama.secrets import CredentialVault


def stop(process):
    if process.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        process.wait(timeout=10)


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Smoke-test a frozen executable")
    parser.add_argument("--binary", type=Path, default=root / "dist" / __version__ / "AnyLlama.exe")
    parser.add_argument(
        "--default-port", action="store_true", help="Verify first launch on default port 11434"
    )
    parser.add_argument(
        "--skip-credential-store", action="store_true", help="For noninteractive CI runners only"
    )
    parser.add_argument("--skip-tray", action="store_true", help="For noninteractive CI runners only")
    options = parser.parse_args()
    binary = options.binary.resolve()
    data = root / ".test-data" / ("exe-" + uuid.uuid4().hex[:8])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 11434 if options.default_port else 0))
        port = sock.getsockname()[1]
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    args = [str(binary), "--headless", "--no-browser", "--no-update-check", "--data-dir", str(data)]
    if not options.default_port:
        args.extend(["--port", str(port)])
    kwargs = {"env": env, "creationflags": subprocess.CREATE_NO_WINDOW}
    process = subprocess.Popen(args, **kwargs)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False)
    credential_id = None

    def ready():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert process.poll() is None, "Frozen application exited during startup"
            try:
                if client.get("/api/version").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("Frozen application did not start")
        info = json.loads((data / "instance.json").read_text())
        token = parse_qs(urlsplit(info["url"]).fragment)["token"][0]
        response = client.post("/admin/api/session", headers={"Authorization": "Bearer " + token})
        response.raise_for_status()
        return {"X-CSRF-Token": response.json()["csrf"]}, info

    try:
        headers, first = ready()
        assert client.get("/api/version").json()["anyllama_version"] == __version__
        alias = client.post(
            "/chat/completions",
            json={"model": "smoke-missing", "messages": [{"role": "user", "content": "test"}]},
        )
        assert alias.json()["error"]["code"] == "model_not_found"
        assert client.get("/admin").status_code == 200
        assert client.get("/admin/static/htmx.min.js").status_code == 200
        response = client.post(
            "/admin/api/providers",
            headers=headers,
            json={
                "name": "EXE smoke test",
                "kind": "custom",
                "base_url": "http://127.0.0.1:9/v1",
                "api_key": None if options.skip_credential_store else "temporary-smoke-key",
            },
        )
        response.raise_for_status()
        credential_id = response.json()["id"]
        vault = CredentialVault()
        if not options.skip_credential_store:
            assert vault.get(credential_id) == "temporary-smoke-key"
        assert "temporary-smoke-key" not in client.get("/admin/api/export").text
        assert client.delete(f"/admin/api/providers/{credential_id}", headers=headers).status_code == 200
        if not options.skip_credential_store:
            assert vault.get(credential_id) is None
        credential_id = None
        settings = client.get("/admin/api/state").json()["settings"]
        settings["theme"] = "dark"
        assert client.put("/admin/api/settings", headers=headers, json=settings).status_code == 200

        duplicate = subprocess.Popen(args, **kwargs)
        try:
            assert duplicate.wait(timeout=15) == 0
        finally:
            stop(duplicate)
        assert json.loads((data / "instance.json").read_text())["pid"] == first["pid"]
        stop(process)
        process = subprocess.Popen(args, **kwargs)
        headers, second = ready()
        assert first["pid"] != second["pid"]
        assert client.get("/admin/api/state").json()["settings"]["theme"] == "dark"
        stop(process)
        if not options.skip_tray:
            tray_args = [arg for arg in args if arg != "--headless"]
            process = subprocess.Popen(tray_args, **kwargs)
            ready()
            time.sleep(2)
            assert process.poll() is None, "Tray application exited after server startup"
        print(
            json.dumps(
                {
                    "result": "passed",
                    "binary": binary.name,
                    "version": __version__,
                    "bytes": binary.stat().st_size,
                    "scenarios": [
                        "bundled runtime with Python removed from PATH",
                        "API",
                        "dashboard assets",
                        "credential store skipped"
                        if options.skip_credential_store
                        else "Windows Credential Manager write/read/delete",
                        "single instance",
                        "restart persistence",
                        "tray skipped" if options.skip_tray else "tray process startup",
                    ],
                }
            )
        )
    finally:
        if credential_id and not options.skip_credential_store:
            with contextlib.suppress(Exception):
                CredentialVault().delete(credential_id)
        client.close()
        stop(process)


if __name__ == "__main__":
    main()
