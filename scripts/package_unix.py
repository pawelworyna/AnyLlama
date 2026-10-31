"""Package native Linux builds as .deb and native macOS builds as a ZIP."""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from package_release import collect_notices

from anyllama import __version__
from anyllama.branding import create_icon

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / __version__


def main():
    machine = platform.machine().lower()
    notices, _ = collect_notices(ROOT)
    if sys.platform == "darwin" and machine == "arm64":
        bundle = DIST / "AnyLlama.app"
        if not bundle.is_dir():
            raise SystemExit("Build AnyLlama.app first")
        archive = DIST / f"AnyLlama-{__version__}-macos-arm64.zip"
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle),
             str(archive)], check=True,
        )
        with tempfile.TemporaryDirectory(prefix="anyllama-notices-") as temp:
            notice_file = Path(temp) / "THIRD_PARTY_NOTICES.txt"
            notice_file.write_text(notices, encoding="utf-8")
            subprocess.run(["zip", "-j", str(archive), str(ROOT / "LICENSE"), str(notice_file)], check=True)
        return
    if sys.platform != "linux" or machine not in {"x86_64", "aarch64"}:
        raise SystemExit("Unsupported platform or architecture")
    arch = "amd64" if machine == "x86_64" else "arm64"
    libc, libc_version = platform.libc_ver()
    if libc != "glibc" or not libc_version:
        raise SystemExit("A glibc-based Linux build host is required")
    binary = DIST / "AnyLlama"
    if not binary.is_file():
        raise SystemExit("Build the Linux binary first")
    with tempfile.TemporaryDirectory(prefix="anyllama-deb-") as temp:
        root = Path(temp)
        root.chmod(0o755)
        for directory in ("DEBIAN", "usr/lib/anyllama", "usr/bin", "usr/share/applications",
                          "usr/share/icons/hicolor/256x256/apps", "usr/share/doc/anyllama"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        control = (
            f"Package: anyllama\nVersion: {__version__}\nArchitecture: {arch}\n"
            "Maintainer: AnyLlama contributors <noreply@github.com>\n"
            f"Depends: libc6 (>= {libc_version}), libstdc++6, zlib1g, xdg-utils, x-terminal-emulator\n"
            "Section: utils\nPriority: optional\n"
            "Homepage: https://github.com/pawelworyna/AnyLlama\n"
            "Description: Local proxy for AI model providers\n"
            " AnyLlama exposes selected models through Ollama and OpenAI-compatible APIs.\n"
        )
        (root / "DEBIAN/control").write_text(control, encoding="utf-8")
        target = root / "usr/lib/anyllama/AnyLlama"
        shutil.copy2(binary, target)
        target.chmod(0o755)
        launcher = root / "usr/bin/anyllama"
        launcher.write_text('#!/bin/sh\nexec /usr/lib/anyllama/AnyLlama --headless --open-browser "$@"\n',
                            encoding="utf-8")
        launcher.chmod(0o755)
        (root / "usr/share/applications/anyllama.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=AnyLlama\nExec=anyllama\n"
            "Icon=anyllama\nTerminal=true\nCategories=Development;Utility;\n", encoding="utf-8"
        )
        create_icon(size=256).save(root / "usr/share/icons/hicolor/256x256/apps/anyllama.png")
        copyright_file = root / "usr/share/doc/anyllama/copyright"
        shutil.copyfile(ROOT / "LICENSE", copyright_file)
        copyright_file.chmod(0o644)
        (root / "usr/share/doc/anyllama/THIRD_PARTY_NOTICES.txt").write_text(notices, encoding="utf-8")
        os.chmod(root / "DEBIAN", 0o755)
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(root),
                        str(DIST / f"anyllama_{__version__}_{arch}.deb")], check=True)


if __name__ == "__main__":
    main()
