"""Smoke-test a native frozen executable without a graphical session or real secrets."""

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from anyllama import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    binary = parser.parse_args().binary.resolve()
    with tempfile.TemporaryDirectory(prefix="anyllama-smoke-") as data:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        command = [str(binary), "--headless", "--no-browser", "--no-update-check", "--data-dir", data,
                   "--port", str(port)]
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}}
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False) as client:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Frozen application exited before starting")
                    try:
                        response = client.get("/api/version")
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Frozen application did not start")
                assert response.json()["anyllama_version"] == __version__
                instance = json.loads((Path(data) / "instance.json").read_text(encoding="utf-8"))
                token = parse_qs(urlsplit(instance["url"]).fragment)["token"][0]
                session = client.post("/admin/api/session", headers={"Authorization": f"Bearer {token}"})
                session.raise_for_status()
                headers = {"X-CSRF-Token": session.json()["csrf"]}
                assert client.get("/admin/api/state", headers=headers).status_code == 200
                assert client.get("/v1/models").json()["data"] == []
                subprocess.run(command, capture_output=True, timeout=15, check=True, env=env)
                after = json.loads((Path(data) / "instance.json").read_text(encoding="utf-8"))
                assert after["pid"] == instance["pid"]
        finally:
            process.terminate()
            try:
                _, error = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                _, error = process.communicate()
            if process.returncode not in (0, -15):
                print(error.decode("utf-8", errors="replace"))
                raise RuntimeError(f"Frozen application exited with status {process.returncode}")
    print("Native frozen app smoke test passed")


if __name__ == "__main__":
    main()
