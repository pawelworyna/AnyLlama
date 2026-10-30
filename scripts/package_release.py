"""Package an already-built EXE with instructions and bundled dependency notices."""

import argparse
import hashlib
import importlib.metadata as metadata
import sys
import zipfile
from pathlib import Path

from packaging.requirements import Requirement

from anyllama import __version__


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Package an existing Windows executable")
    parser.add_argument("--dist-dir", type=Path, default=root / "dist" / __version__)
    dist = parser.parse_args().dist_dir.resolve()
    binary = dist / "AnyLlama.exe"
    if not binary.is_file():
        raise SystemExit("Build the EXE first: uv run python scripts/build.py")
    notices = [
        "AnyLlama — third-party notices\n",
        "Dependency and build-tool versions with their installed license files.\n",
    ]
    pending = [Requirement(r) for r in metadata.requires("anyllama") or []]
    pending.append(Requirement("pyinstaller"))
    seen = set()
    while pending:
        requirement = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        distribution = metadata.distribution(requirement.name)
        name = distribution.metadata["Name"]
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        notices.append(f"\n{'=' * 72}\n{name} {distribution.version}\n")
        notices.extend(distribution.metadata.get_all("Project-URL") or [])
        for item in distribution.files or []:
            if ".dist-info/" not in str(item).replace("\\", "/"):
                continue
            if item.name.lower().startswith(("license", "copying", "notice")):
                path = distribution.locate_file(item)
                if path.is_file():
                    notices.append(path.read_text(encoding="utf-8", errors="replace"))
        pending.extend(Requirement(r) for r in distribution.requires or [])
    notices.append("\nHTMX 2.0.8\nhttps://github.com/bigskysoftware/htmx\n")
    notices.append((root / "src/anyllama/static/HTMX-LICENSE.txt").read_text())
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        notices.append("\nPython runtime\n" + python_license.read_text(encoding="utf-8"))
    notice_file = dist / "THIRD_PARTY_NOTICES.txt"
    notice_file.write_text("\n".join(notices), encoding="utf-8")
    archive = dist / f"AnyLlama-{__version__}-win-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.write(binary, binary.name)
        package.write(notice_file, notice_file.name)
        for name in (
            "LICENSE",
            "README.md",
            "CHANGELOG.md",
            "docs/RELEASING.md",
            "docs/VALIDATION.md",
            "docs/ARCHITECTURE.md",
            "docs/PROVIDERS.md",
            "docs/REVIEW.md",
            "docs/DEPENDENCY-AUDIT.json",
            "docs/screenshots/overview.png",
        ):
            package.write(root / name, name)
    assets = [binary, archive, notice_file]
    if (validation := dist / "validation.json").is_file():
        assets.append(validation)
    checksums = []
    for path in sorted(assets):
        with path.open("rb") as source:
            checksums.append(f"{hashlib.file_digest(source, 'sha256').hexdigest()}  {path.name}\n")
    (dist / "SHA256SUMS.txt").write_text("".join(checksums), encoding="utf-8")
    print(f"Release: {archive}\nDependency notices: {len(seen)} packages + Python + HTMX")


if __name__ == "__main__":
    main()
