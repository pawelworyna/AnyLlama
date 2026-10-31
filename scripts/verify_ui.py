"""Browser acceptance using isolated data and a fake provider; no real API calls."""

import argparse
import asyncio
import json
import socket
import threading
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from playwright.sync_api import sync_playwright

from anyllama.app import create_app


class TestVault:
    def __init__(self):
        self.keys = {}

    def get(self, key):
        return self.keys.get(key)

    def set(self, key, value):
        self.keys[key] = value

    def delete(self, key):
        self.keys.pop(key, None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", choices=("chrome", "chromium"), default="chrome")
    parser.add_argument("--screenshots-dir", type=Path)
    options = parser.parse_args()
    browser_channel = options.browser
    root = Path(__file__).resolve().parents[1]
    data = root / ".test-data" / ("ui-" + uuid.uuid4().hex[:8])
    screenshots = options.screenshots_dir or root / "docs" / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)

    def provider(request):
        if request.url.host == "api.github.com":
            return httpx.Response(
                200,
                json={
                    "tag_name": "v99.0.0",
                    "draft": False,
                    "prerelease": False,
                    "html_url": "javascript:ignored-by-proxy",
                    "assets": [{"name": "AnyLlama-99.0.0-win-x64.zip", "state": "uploaded", "size": 100}],
                },
            )
        if request.url.path.endswith("/chat/completions"):

            class SampleStream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    for text in ("Sample ", "answer"):
                        await asyncio.sleep(0.05)
                        yield (
                            "data: "
                            + json.dumps(
                                {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
                            )
                            + "\n\n"
                        ).encode()
                    usage = {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "prompt_tokens_details": {"cached_tokens": 20},
                        "completion_tokens_details": {"reasoning_tokens": 4},
                    }
                    if "reported" in json.loads(request.content)["messages"][0]["content"]:
                        usage["cost"] = 0.00012
                    yield (
                        "data: "
                        + json.dumps(
                            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage}
                        )
                        + "\n\ndata: [DONE]\n\n"
                    ).encode()

            return httpx.Response(200, stream=SampleStream())
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "sample/coding-model",
                        "name": "Sample Coding Model",
                        "context_length": 128000,
                        "supported_parameters": ["tools", "reasoning"],
                        "architecture": {"input_modalities": ["text", "image"]},
                        "pricing": {
                            "prompt": "0.00000125",
                            "completion": "0.000005",
                            "input_cache_read": "0.00000025",
                        },
                    },
                    {
                        "id": "sample/fast-model",
                        "name": "Sample Fast Model",
                        "context_length": 64000,
                        "supported_parameters": ["tools"],
                    },
                ]
            },
        )

    app = create_app(
        data,
        vault=TestVault(),
        refresh_interval=0,
        update_interval=0,
        transport=httpx.MockTransport(provider),
        bootstrap_token="ui-test-only",
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app.state.bound_port = port
    app.state.store.save_settings(app.state.store.settings().model_copy(update={"port": port}))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="chrome" if browser_channel == "chrome" else None, headless=True
            )
            page = browser.new_page(viewport={"width": 1440, "height": 1060}, color_scheme="light")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console", lambda message: errors.append(message.text) if message.type == "error" else None
            )
            page.goto(f"http://127.0.0.1:{port}/admin/#token=ui-test-only")
            page.get_by_role("heading", name="Bring a provider to your workspace").wait_for()
            # Changing profiles fills the correct API URL and clears a key typed for another vendor.
            presets = app.state.store.export()  # Configuration starts empty; no real credentials are used.
            assert not presets["providers"]
            from anyllama.models import PRESETS

            selector = page.get_by_label("Provider", exact=True)
            assert selector.locator("option").count() == 19
            for kind, preset in PRESETS.items():
                page.get_by_label("API key", exact=True).fill("previous-provider-fixture-key")
                selector.select_option(kind)
                assert page.get_by_label("API base URL", exact=True).input_value() == preset["base_url"]
                if kind != "openrouter":
                    assert page.get_by_label("API key", exact=True).input_value() == ""
                assert page.get_by_label("Connection name", exact=True).input_value() == preset["name"]
                assert page.get_by_label("API key", exact=True).evaluate("el => el.required") == preset.get(
                    "requires_key", True
                )
            selector.select_option("gemini")
            assert "thought signatures" in page.locator("#provider-note").inner_text()
            selector.select_option("openrouter")
            page.screenshot(path=str(screenshots / "setup.png"))
            page.get_by_label("Connection name").fill("Sample OpenRouter")
            page.get_by_label("API key", exact=True).fill("fixture-key-not-a-real-credential")
            page.get_by_role("button", name="Save & check connection").click()
            page.get_by_role("heading", name="Choose your models").wait_for()
            page.get_by_role("checkbox", name="Sample Coding Model").check()
            page.get_by_role("button", name="Save models & continue").click()
            page.get_by_role("heading", name="Your next stop: Visual Studio").wait_for()
            page.get_by_role("button", name="Done, let’s build").click()
            page.get_by_role("heading", name="Your workspace, connected.").wait_for()
            page.screenshot(path=str(screenshots / "overview.png"), full_page=True)

            page.get_by_role("link", name="Models", exact=True).click()
            selected = next(m for m in app.state.store.models() if m["upstream_id"] == "sample/coding-model")
            app.state.store.patch_model(
                selected["id"], {"context_length": '"><span id="injected-limit">bad</span>'}
            )
            # Exercise the UI against a legacy record predating catalog validation.
            page.reload()
            page.get_by_role("button", name="Configure").first.wait_for()
            assert page.get_by_role("checkbox", name="Enable Sample Coding Model").is_checked()
            page.get_by_role("button", name="Configure").first.click()
            assert page.locator("#injected-limit").count() == 0
            page.get_by_label("Context tokens").fill("256000")
            page.get_by_text("Pricing (optional · USD per 1M tokens)", exact=True).click()
            assert page.get_by_label("Input price", exact=True).input_value() == "1.25"
            page.get_by_label("Output price", exact=True).fill("6.25")
            page.get_by_role("button", name="Save model", exact=True).click()
            page.get_by_text("256,000", exact=True).wait_for()
            assert app.state.store.model(selected["id"])["output_price_per_million"] == 6.25
            page.get_by_role("searchbox").fill("no-such-model")
            page.get_by_role("heading", name="No matching models").wait_for()
            page.get_by_role("searchbox").fill("")
            page.get_by_role("button", name="Add model manually").click()
            page.get_by_label("Provider model ID").fill("sample/manual-model")
            page.get_by_label("Display name").fill("Sample manual model")
            page.get_by_role("button", name="Save model", exact=True).click()
            page.get_by_text("Sample manual model", exact=True).wait_for()
            page.screenshot(path=str(screenshots / "models.png"), full_page=True)

            page.get_by_role("link", name="Settings", exact=True).click()
            page.get_by_role("button", name="Check for updates", exact=True).click()
            page.get_by_text("Version 99.0.0 is available.", exact=False).wait_for()
            assert (
                page.locator("#update-link").get_attribute("href")
                == "https://github.com/pawelworyna/AnyLlama/releases/tag/v99.0.0"
            )
            page.get_by_role("checkbox", name="Check for updates automatically").uncheck()
            original_port = page.get_by_label("Proxy port", exact=True).input_value()
            page.get_by_role("button", name="Use Ollama default (11434)", exact=True).click()
            assert page.get_by_label("Proxy port", exact=True).input_value() == "11434"
            # The preset only edits the form; it must not save or disrupt the running endpoint.
            assert app.state.store.settings().port == port
            page.get_by_label("Proxy port", exact=True).fill(original_port)
            page.get_by_label("Theme", exact=True).select_option("dark")
            page.get_by_role("button", name="Save settings").click()
            page.locator('html[data-theme="dark"]').wait_for()
            assert not app.state.store.settings().check_updates
            page.evaluate("document.activeElement?.blur(); window.scrollTo(0,0)")
            page.locator(".toast").last.wait_for(state="hidden")
            page.screenshot(path=str(screenshots / "settings-dark.png"), full_page=True)
            page.get_by_label("Theme", exact=True).select_option("light")
            page.get_by_role("button", name="Save settings").click()
            page.locator('html[data-theme="light"]').wait_for()
            page.get_by_role("link", name="Overview", exact=True).click()
            page.get_by_role("button", name="Pause proxy").click()
            page.get_by_role("button", name="Resume proxy").wait_for()
            page.get_by_role("button", name="Resume proxy").click()
            page.get_by_role("button", name="Pause proxy").wait_for()
            page.get_by_role("link", name="Providers", exact=True).click()
            page.get_by_role("button", name="Edit", exact=True).click()
            page.get_by_label("Connection name").fill("Sample connection renamed")
            page.get_by_role("button", name="Save connection").click()
            page.get_by_role("heading", name="Sample connection renamed").wait_for()
            page.get_by_role("button", name="Add provider", exact=False).click()
            page.get_by_label("Provider", exact=True).select_option("groq")
            page.get_by_label("API key", exact=True).fill("fixture-groq-key")
            page.get_by_role("button", name="Save & check connection", exact=False).click()
            page.get_by_role("heading", name="Choose your models").wait_for()
            page.get_by_role("checkbox", name="Sample Fast Model").check()
            page.get_by_role("button", name="Save models & continue").click()
            page.get_by_role("button", name="Done, let’s build").click()
            page.get_by_role("link", name="Providers", exact=True).click()
            page.get_by_role("heading", name="Groq", exact=True).wait_for()
            page.screenshot(path=str(screenshots / "providers.png"), full_page=True)
            page.get_by_role("link", name="Activity", exact=True).click()
            page.get_by_role("heading", name="A little quiet here").wait_for()
            # Run normal proxy calls through the fake provider; never contacts a real account.
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                for mode in ("reported", "estimate"):
                    client.post(
                        "/api/chat",
                        json={"model": selected["id"], "messages": [{"role": "user", "content": mode}]},
                    ).raise_for_status()
            app.state.store.record(
                model="sample/legacy",
                provider="Sample provider",
                status=499,
                duration_ms=100,
                error="client_disconnected",
            )
            page.get_by_role("button", name="Refresh", exact=False).click()
            page.locator("td").filter(has_text="$0.000120").wait_for()
            page.locator("td").filter(has_text="$0.000230").wait_for()
            page.get_by_text("Reported", exact=True).wait_for()
            page.get_by_text("Estimate", exact=True).wait_for()
            page.get_by_text("Cancelled", exact=True).wait_for()
            assert "≈" in page.locator(".activity-table").inner_text()
            assert "20 cached input" in page.locator(".activity-table").inner_text()
            assert page.locator(".activity-table tbody tr").count() == 3
            page.screenshot(path=str(screenshots / "activity.png"), full_page=True)
            page.set_viewport_size({"width": 720, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

            page.set_viewport_size({"width": 720, "height": 900})
            page.get_by_role("link", name="Overview", exact=True).click()
            page.locator(".toast").last.wait_for(state="hidden")
            page.screenshot(path=str(screenshots / "overview-mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors, errors
            browser.close()
            print(
                json.dumps(
                    {
                        "browser": browser_channel,
                        "result": "passed",
                        "console_errors": errors,
                        "scenarios": [
                            "onboarding",
                            "provider",
                            "selection",
                            "manual model",
                            "metadata",
                            "search",
                            "theme",
                            "pause",
                            "navigation",
                            "responsive",
                            "pricing overrides",
                            "Ollama default port preset",
                            "19 provider choices, URL autofill and optional local keys",
                            "new provider onboarding",
                            "update notification and opt-out",
                            "real-request metrics with fake provider",
                        ],
                        "screenshots": str(screenshots),
                    }
                )
            )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


if __name__ == "__main__":
    main()
