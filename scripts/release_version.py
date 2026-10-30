"""Check or bump the three version declarations without changing dependencies."""

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from anyllama.versioning import stable_version  # noqa: E402


def current_version(root=ROOT):
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    versions = [project["project"]["version"]]
    packages = [p["version"] for p in lock["package"] if p["name"] == "anyllama"]
    match = re.search(
        r'^__version__ = "([^"]+)"$', (root / "src/anyllama/__init__.py").read_text(encoding="utf-8"), re.M
    )
    if len(packages) != 1 or not match:
        raise ValueError("Cannot find the package version declarations")
    versions.extend([packages[0], match.group(1)])
    version = versions[0]
    parsed = stable_version(version)
    if len(set(versions)) != 1 or not parsed or version != ".".join(map(str, parsed)):
        raise ValueError("Versions must match and use stable MAJOR.MINOR.PATCH syntax")
    return version


def bump_version(part, root=ROOT):
    old = current_version(root)
    major, minor, patch = stable_version(old)
    if part == "major":
        new = f"{major + 1}.0.0"
    elif part == "minor":
        new = f"{major}.{minor + 1}.0"
    elif part == "patch":
        new = f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError("Choose patch, minor or major")
    changes = {
        root / "pyproject.toml": (rf'(?m)^version = "{re.escape(old)}"$', f'version = "{new}"'),
        root / "src/anyllama/__init__.py": (
            rf'(?m)^__version__ = "{re.escape(old)}"$',
            f'__version__ = "{new}"',
        ),
        root / "uv.lock": (
            rf'(\[\[package\]\]\nname = "anyllama"\nversion = "){re.escape(old)}(")',
            rf"\g<1>{new}\g<2>",
        ),
    }
    pending = {}
    for path, (pattern, replacement) in changes.items():
        updated, count = re.subn(pattern, replacement, path.read_text(encoding="utf-8"), count=1)
        if count != 1:
            raise ValueError(f"Cannot update version in {path.name}")
        pending[path] = updated
    for path, text in pending.items():
        path.write_text(text, encoding="utf-8", newline="\n")
    return new


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "bump"))
    parser.add_argument("--part", choices=("patch", "minor", "major"))
    parser.add_argument("--tag", help="Require an exact vMAJOR.MINOR.PATCH tag")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        version = bump_version(args.part, args.root) if args.command == "bump" else current_version(args.root)
        if args.tag and args.tag != f"v{version}":
            raise ValueError("Release tag does not match the application version")
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as target:
            target.write(f"version={version}\ntag=v{version}\n")
    print(version)


if __name__ == "__main__":
    main()
