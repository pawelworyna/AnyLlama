"""Read-only GitHub release checks. No updater downloads or executes code."""

import asyncio
import json
import sys
import sysconfig
from datetime import datetime, timezone

import httpx

from . import __version__
from .versioning import stable_version

REPOSITORY = "pawelworyna/AnyLlama"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
MAX_RESPONSE_BYTES = 256 * 1024


class UpdateError(Exception):
    pass


def expected_asset(version: str) -> str | None:
    platform = sysconfig.get_platform().lower()
    if sys.platform == "win32":
        arch = "arm64" if "arm64" in platform else "x64" if "amd64" in platform else None
        return f"AnyLlama-{version}-win-{arch}.zip" if arch else None
    if sys.platform == "darwin" and ("arm64" in platform or "universal2" in platform):
        return f"AnyLlama-{version}-macos-arm64.zip"
    if sys.platform.startswith("linux"):
        arch = "arm64" if "aarch64" in platform else "amd64" if "x86_64" in platform else None
        return f"anyllama_{version}_{arch}.deb" if arch else None
    return None


class UpdateChecker:
    def __init__(self, client, store, current_version=__version__):
        self.client, self.store, self.current_version = client, store, current_version
        self.lock = asyncio.Lock()

    def status(self):
        saved = self.store.update_state()
        if saved.get("repository") != REPOSITORY:
            saved = {}
        tag = saved.get("latest_tag")
        latest, current = stable_version(tag), stable_version(self.current_version)
        available = bool(latest and current and latest > current)
        error = saved.get("error")
        return {
            "repository": REPOSITORY,
            "current_version": self.current_version,
            "latest_version": tag.removeprefix("v") if latest else None,
            "available": available,
            "checked_at": saved.get("checked_at"),
            "release_url": f"{RELEASES_URL}/tag/{tag}" if latest else RELEASES_URL,
            "error": error if error in {"network", "rate_limit", "no_release", "invalid_release"} else None,
        }

    async def check(self, *, force=False):
        async with self.lock:
            saved = self.store.update_state()
            if saved.get("repository") != REPOSITORY:
                saved = {}
            now = datetime.now(timezone.utc)
            try:
                age = (now - datetime.fromisoformat(saved["checked_at"])).total_seconds()
            except (KeyError, TypeError, ValueError):
                age = None
            ttl = 60 if force else 3600 if saved.get("error") else 86400
            if age is not None and 0 <= age < ttl:
                return self.status()
            result = {**saved, "repository": REPOSITORY, "checked_at": now.isoformat(), "error": None}
            try:
                async with asyncio.timeout(15):
                    data = await self._release()
                tag = data.get("tag_name")
                latest, current = stable_version(tag), stable_version(self.current_version)
                if not latest or not current or data.get("draft") or data.get("prerelease"):
                    raise UpdateError("invalid_release")
                if latest > current:
                    expected = expected_asset(tag.removeprefix("v"))
                    assets = data.get("assets")
                    if not expected or not isinstance(assets, list) or not any(
                        isinstance(asset, dict)
                        and asset.get("name") == expected
                        and asset.get("state") == "uploaded"
                        and isinstance(asset.get("size"), int)
                        and not isinstance(asset["size"], bool)
                        and asset["size"] > 0
                        for asset in assets
                    ):
                        raise UpdateError("invalid_release")
                result["latest_tag"] = tag
            except UpdateError as exc:
                result["error"] = str(exc)
            except (httpx.HTTPError, TimeoutError):
                result["error"] = "network"
            except (ValueError, TypeError):
                result["error"] = "invalid_release"
            self.store.save_update_state(result)
            return self.status()

    async def _release(self):
        url = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
        async with self.client.stream(
            "GET",
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": f"AnyLlama/{self.current_version}",
            },
            timeout=httpx.Timeout(10, connect=5),
            follow_redirects=False,
        ) as response:
            if response.status_code == 404:
                raise UpdateError("no_release")
            if response.status_code in {403, 429}:
                raise UpdateError("rate_limit")
            if response.status_code != 200:
                raise UpdateError("network")
            body = bytearray()
            async for block in response.aiter_bytes():
                if len(body) + len(block) > MAX_RESPONSE_BYTES:
                    raise UpdateError("invalid_release")
                body.extend(block)
        data = json.loads(body)
        if not isinstance(data, dict):
            raise UpdateError("invalid_release")
        return data
