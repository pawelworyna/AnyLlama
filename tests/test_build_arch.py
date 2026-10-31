import pytest

from scripts.check_build_arch import matches


@pytest.mark.parametrize(
    "expected,system,build,machine,valid",
    [
        ("arm64", "darwin", "macosx-10.13-universal2", "arm64", True),
        ("arm64", "darwin", "macosx-11.0-arm64", "arm64", True),
        ("arm64", "darwin", "macosx-10.13-universal2", "x86_64", False),
        ("arm64", "darwin", "macosx-10.13-x86_64", "arm64", False),
        ("win-arm64", "win32", "win-arm64", "arm64", True),
        ("win-arm64", "win32", "win-amd64", "arm64", False),
        ("aarch64", "linux", "linux-aarch64", "aarch64", True),
        ("aarch64", "linux", "linux-x86_64", "x86_64", False),
    ],
)
def test_native_architecture_detection(expected, system, build, machine, valid):
    assert matches(expected, system, build, machine) is valid
