import asyncio
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from conftest import completion, login

from anyllama.storage import Store
from anyllama.updates import MAX_RESPONSE_BYTES, REPOSITORY, UpdateChecker


def release(tag="v1.10.0", **extra):
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": f"AnyLlama-{tag.removeprefix('v')}-win-x64.zip", "state": "uploaded", "size": 100}
        ],
        **extra,
    }


async def test_numeric_version_comparison_cache_and_safe_links(tmp_path):
    store = Store(tmp_path / "updates.db")
    seen = []

    def upstream(req):
        seen.append(req)
        assert str(req.url) == f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
        assert "authorization" not in req.headers
        return httpx.Response(200, json=release(html_url="javascript:bad()", body="private data"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        checker = UpdateChecker(client, store, "1.9.9")
        result = await checker.check()
        assert result["available"] and result["latest_version"] == "1.10.0"
        assert result["release_url"] == f"https://github.com/{REPOSITORY}/releases/tag/v1.10.0"
        assert (await checker.check(force=True)) == result
        assert len(seen) == 1
    assert "private data" not in str(store.update_state())
    store.close()
    reopened = Store(tmp_path / "updates.db")
    assert UpdateChecker(None, reopened, "1.10.0").status()["available"] is False
    reopened.close()


@pytest.mark.parametrize(
    "payload",
    [
        release("v1.10.0-beta"),
        release(draft=True),
        release(prerelease=True),
        release(assets=[]),
        release(assets=[{"name": "AnyLlama-1.10.0-win-x64.zip", "size": 100, "state": "new"}]),
        release("javascript:bad"),
        [],
        {"tag_name": 7},
    ],
)
async def test_invalid_or_incomplete_release_is_not_advertised(tmp_path, payload):
    store = Store(tmp_path / "updates.db")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    ) as client:
        result = await UpdateChecker(client, store, "1.9.9").check()
        assert not result["available"] and result["error"] == "invalid_release"
    store.close()


@pytest.mark.parametrize(
    "code,error",
    [(404, "no_release"), (403, "rate_limit"), (429, "rate_limit"), (500, "network"), (302, "network")],
)
async def test_failed_checks_keep_last_known_release_and_are_cached(tmp_path, code, error):
    store = Store(tmp_path / "updates.db")
    store.save_update_state(
        {
            "repository": REPOSITORY,
            "latest_tag": "v1.10.0",
            "checked_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        }
    )
    seen = []

    def upstream(req):
        seen.append(req)
        return httpx.Response(
            code, headers={"Location": "https://evil.example"}, json={"error": "do not retain"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        checker = UpdateChecker(client, store, "1.9.9")
        result = await checker.check()
        assert result["available"] and result["error"] == error
        await checker.check()
        assert len(seen) == 1
    assert "do not retain" not in str(store.update_state())
    store.close()


async def test_network_error_is_not_propagated_to_proxy(tmp_path):
    store = Store(tmp_path / "updates.db")

    def upstream(req):
        raise httpx.ConnectTimeout("private exception details")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        checker = UpdateChecker(client, store, "1.9.9")
        assert (await checker.check())["error"] == "network"
    assert "private exception" not in str(store.update_state())
    store.close()


async def test_oversized_release_metadata_is_bounded(tmp_path):
    store = Store(tmp_path / "updates.db")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=b" " * (MAX_RESPONSE_BYTES + 1))
        )
    ) as client:
        assert (await UpdateChecker(client, store).check())["error"] == "invalid_release"
    store.close()


async def test_cancelled_check_closes_upstream_and_keeps_cache(tmp_path):
    closed, started = asyncio.Event(), asyncio.Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.sleep(60)
            yield b"{}"

        async def aclose(self):
            closed.set()

    store = Store(tmp_path / "updates.db")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=SlowStream()))
    ) as client:
        task = asyncio.create_task(UpdateChecker(client, store).check())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set() and not store.update_state()
    store.close()


def test_update_endpoint_requires_session_and_csrf(make_client):
    client, app, _, _ = make_client(lambda req: httpx.Response(200, json=release("v99.0.0")))
    assert client.post("/admin/api/updates/check").status_code == 401
    headers = login(client)
    assert client.post("/admin/api/updates/check").status_code == 403
    result = client.post("/admin/api/updates/check", headers=headers)
    assert result.json()["available"] is True
    assert client.get("/admin/api/state").json()["updates"]["latest_version"] == "99.0.0"
    settings = app.state.store.settings().model_dump()
    settings["check_updates"] = False
    client.put("/admin/api/settings", headers=headers, json=settings).raise_for_status()
    assert app.state.store.settings().check_updates is False


def test_slow_background_update_check_does_not_block_generation(make_client):
    started = threading.Event()

    async def upstream(req):
        if req.url.host == "api.github.com":
            assert "authorization" not in req.headers
            started.set()
            await asyncio.sleep(60)
            return httpx.Response(200, json=release())
        return httpx.Response(200, json=completion("Generation works"))

    client, _, model, _ = make_client(upstream, update_interval=0.01)
    assert started.wait(timeout=2)
    response = client.post(
        "/api/chat",
        json={"model": model["id"], "stream": False, "messages": [{"role": "user", "content": "Hello"}]},
    )
    assert response.json()["message"]["content"] == "Generation works"
