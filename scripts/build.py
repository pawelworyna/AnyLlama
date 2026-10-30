"""Run with uv run python scripts/build.py on Windows x64."""

import argparse
import subprocess
import sys
from pathlib import Path

from anyllama import __version__
from anyllama.branding import create_icon, write_svg

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Build the Windows executable")
parser.add_argument("--dist-dir", type=Path, default=root / "dist" / __version__)
args = parser.parse_args()
build = root / "build"
build.mkdir(exist_ok=True)
write_svg(root / "src" / "anyllama" / "static" / "icon.svg")
icon = build / "anyllama.ico"
create_icon(size=256).save(icon, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "AnyLlama",
        "--icon",
        str(icon),
        "--specpath",
        str(build),
        "--distpath",
        str(args.dist_dir.resolve()),
        "--add-data",
        f"{root / 'src' / 'anyllama' / 'templates'};anyllama/templates",
        "--add-data",
        f"{root / 'src' / 'anyllama' / 'static'};anyllama/static",
        "--hidden-import",
        "pystray._win32",
        "--hidden-import",
        "keyring.backends.Windows",
        "--hidden-import",
        "win32ctypes.pywin32.win32cred",
        str(root / "scripts" / "entrypoint.py"),
    ],
    cwd=root,
    check=True,
)
print(f"Built: {args.dist_dir.resolve() / 'AnyLlama.exe'}")
