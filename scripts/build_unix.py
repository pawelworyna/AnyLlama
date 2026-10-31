"""Build on the target macOS or Linux system using its native architecture."""

import argparse
import platform
import subprocess
import sys
from pathlib import Path

from anyllama import __version__
from anyllama.branding import create_icon

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist" / __version__)
    args = parser.parse_args()
    if sys.platform not in {"darwin", "linux"}:
        raise SystemExit("Build this package on the target macOS or Linux platform")
    if platform.machine().lower() not in {"arm64", "aarch64", "x86_64"}:
        raise SystemExit("Unsupported CPU architecture")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    options = ["--onedir", "--windowed"] if sys.platform == "darwin" else ["--onefile", "--console"]
    if sys.platform == "darwin":
        icon = build / "anyllama.png"
        create_icon(size=512).save(icon)
        options += ["--icon", str(icon), "--osx-bundle-identifier", "com.anyllama.app"]
        hidden = ["pystray._darwin", "keyring.backends.macOS"]
    else:
        hidden = ["keyring.backends.SecretService"]
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "AnyLlama",
        "--specpath", str(build), "--distpath", str(args.dist_dir.resolve()),
        "--add-data", f"{ROOT / 'src/anyllama/templates'}:anyllama/templates",
        "--add-data", f"{ROOT / 'src/anyllama/static'}:anyllama/static",
        *options,
    ]
    for module in hidden:
        command.extend(["--hidden-import", module])
    subprocess.run([*command, str(ROOT / "scripts/entrypoint.py")], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
