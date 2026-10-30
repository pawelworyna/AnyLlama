import argparse
import contextlib
import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from .app import create_app
from .branding import create_icon


def default_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "AnyLlama"


class InstanceLock:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "instance.json"
        self.file = (directory / ".instance.lock").open("a+b")
        self.owned = False

    def acquire(self) -> bool:
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.owned = True
            return True
        except OSError:
            return False

    def publish(self, url: str):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"pid": os.getpid(), "url": url}), encoding="utf-8")
        temporary.replace(self.path)

    def existing_url(self) -> str | None:
        for _ in range(20):
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))["url"]
            except (OSError, ValueError, KeyError):
                time.sleep(0.1)
        return None

    def close(self):
        if self.owned:
            self.path.unlink(missing_ok=True)
            self.file.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.owned = False
        self.file.close()


def set_autostart(enabled: bool, data_dir: Path):
    if sys.platform != "win32":
        raise OSError("Windows autostart is not available")
    import winreg

    if getattr(sys, "frozen", False):
        args = [sys.executable, "--startup", "--data-dir", str(data_dir)]
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        args = [
            str(pythonw if pythonw.exists() else sys.executable),
            "-m",
            "anyllama",
            "--startup",
            "--data-dir",
            str(data_dir),
        ]
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            winreg.SetValueEx(key, "AnyLlama", 0, winreg.REG_SZ, subprocess.list2cmdline(args))
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, "AnyLlama")


def show_error(message: str, headless=False):
    if sys.platform == "win32" and not headless:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "AnyLlama", 0x10)
    elif sys.stderr:
        print(message, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="AnyLlama local model proxy")
    parser.add_argument("--headless", action="store_true", help="Run in the foreground without a tray icon")
    parser.add_argument("--startup", action="store_true", help="Start quietly after Windows sign-in")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser on launch")
    parser.add_argument(
        "--no-update-check", action="store_true", help="Disable background update checks for this run"
    )
    parser.add_argument("--port", type=int, help="Save a new proxy port (1024–65535)")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    instance = InstanceLock(data_dir)
    if not instance.acquire():
        existing = instance.existing_url()
        if existing and not args.no_browser and not args.headless and not args.startup:
            webbrowser.open(existing)
        if args.headless and sys.stdout:
            print("AnyLlama is already running for this data directory.", flush=True)
        instance.close()
        return
    app = None
    sock = None
    try:
        handler = RotatingFileHandler(
            data_dir / "anyllama.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        logging.basicConfig(
            level=logging.WARNING, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        app = create_app(
            data_dir,
            apply_autostart=lambda enabled: set_autostart(enabled, data_dir),
            update_interval=0 if args.no_update_check else 300,
        )
        if args.port is not None:
            if not 1024 <= args.port <= 65535:
                parser.error("--port must be between 1024 and 65535")
            settings = app.state.store.settings().model_copy(update={"port": args.port})
            app.state.store.save_settings(settings)
            app.state.bound_port = args.port
        port = app.state.store.settings().port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", port))
            sock.listen(128)
            sock.setblocking(False)
        except OSError:
            show_error(
                f"Port {port} is already in use. Close the other process or run "
                "AnyLlama.exe --port 11436 to save a different port.",
                args.headless,
            )
            raise SystemExit(1)
        url = f"http://127.0.0.1:{port}/admin/#token={app.state.bootstrap_token}"
        instance.publish(url)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
            server_header=False,
            timeout_graceful_shutdown=5,
        )
        server = uvicorn.Server(config)
        if args.headless:
            if sys.stdout:
                print(f"AnyLlama dashboard (private link): {url}", flush=True)
            server.run(sockets=[sock])
        else:
            import pystray

            thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
            thread.start()
            for _ in range(100):
                if server.started or not thread.is_alive():
                    break
                time.sleep(0.05)
            if not server.started:
                show_error(f"AnyLlama could not start. See {data_dir / 'anyllama.log'}.")
                server.should_exit = True
                thread.join(timeout=6)
                raise SystemExit(1)

            stopping = threading.Event()

            def toggle(icon, item):
                app.state.paused = not app.state.paused
                icon.icon = create_icon(app.state.paused)
                icon.title = "AnyLlama — paused" if app.state.paused else "AnyLlama — running"
                icon.update_menu()

            def stop(icon, item):
                server.should_exit = True
                stopping.set()
                icon.stop()

            def watch_state(icon):
                icon.visible = True
                previous = app.state.paused
                while not stopping.wait(0.5):
                    if previous != app.state.paused:
                        previous = app.state.paused
                        icon.icon = create_icon(previous)
                        icon.title = "AnyLlama — paused" if previous else "AnyLlama — running"
                        icon.update_menu()

            icon = pystray.Icon(
                "AnyLlama",
                create_icon(),
                "AnyLlama — running",
                menu=pystray.Menu(
                    pystray.MenuItem("Open dashboard", lambda icon, item: webbrowser.open(url), default=True),
                    pystray.MenuItem(
                        lambda item: "Resume proxy" if app.state.paused else "Pause proxy", toggle
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Exit AnyLlama", stop),
                ),
            )
            if not args.no_browser and not args.startup:
                webbrowser.open(url)
            try:
                icon.run(setup=watch_state)
            finally:
                stopping.set()
                server.should_exit = True
                thread.join(timeout=7)
    finally:
        if sock:
            sock.close()
        # lifespan normally closes this connection; bind failures happen before startup.
        if app:
            with contextlib.suppress(sqlite3.ProgrammingError):
                app.state.store.close()
        instance.close()
