import asyncio
import socket
import threading
import time

import httpx
import uvicorn
from conftest import MemoryVault, chunk, sse

from anyllama.app import create_app


def test_client_disconnect_closes_upstream_and_records_cancellation(tmp_path):
    closed = threading.Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse(chunk({"content": "First token"}), done=False).encode()
            await asyncio.sleep(60)

        async def aclose(self):
            closed.set()

    app = create_app(
        tmp_path,
        vault=MemoryVault(),
        refresh_interval=0,
        update_interval=0,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=SlowStream())),
    )
    app.state.store.save_provider(
        {"name": "Slow", "kind": "custom", "base_url": "https://provider.test", "has_key": False}, "slow"
    )
    model = app.state.store.add_model("slow", {"upstream_id": "slow-model", "name": "Slow", "enabled": True})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        with httpx.Client() as client:
            with client.stream(
                "POST",
                f"http://127.0.0.1:{port}/api/chat",
                json={"model": model["id"], "messages": [{"role": "user", "content": "hello"}]},
            ) as response:
                assert next(response.iter_lines())
        assert closed.wait(timeout=3), "Upstream connection was not closed after downstream disconnect"
        deadline = time.monotonic() + 2
        while not app.state.store.activity() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert app.state.store.activity()[0]["status"] == 499
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
