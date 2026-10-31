"""Ensure the release uses a Python interpreter for the intended architecture."""

import os
import platform
import sys
import sysconfig


def matches(expected: str, system: str, build: str, machine: str) -> bool:
    if system == "darwin":
        return expected == "arm64" and machine == "arm64" and ("arm64" in build or "universal2" in build)
    return expected in build


if __name__ == "__main__":
    expected = os.environ["EXPECTED_ARCH"].lower()
    build = sysconfig.get_platform().lower()
    machine = platform.machine().lower()
    print(f"Python platform: {build}; process architecture: {machine}")
    if not matches(expected, sys.platform, build, machine):
        raise SystemExit(f"Expected native {expected}; got {build} running as {machine}")
