# Publishing and releases

Repository: **https://github.com/pawelworyna/AnyLlama**. License: MIT.
The project is prepared locally; no push, remote repository creation or remote release
is performed by the preparation scripts.

## First publication

The local Git repository uses `main` with `origin` set to the URL above. Create an empty
public GitHub repository if it does not exist yet, then inspect and push the source:

```powershell
git status --short
git add .
git diff --cached --stat
git commit -m "Initial AnyLlama release"
git push -u origin main
```

If the remote already contains commits, integrate that history first; do not force-push
over it. Runtime data, virtual environments, build products, local exports, `.env` files,
keys and logs are ignored. Test credentials are clearly synthetic. Never stage a real
configuration export or dashboard bootstrap link.

1. Let **CI** pass after the first push. It runs on Windows with Python 3.13, the frozen
   uv lockfile, lint, unit/integration tests and an isolated Chromium dashboard test.
2. In repository **Settings → Actions → General**, allow GitHub Actions to create pull
   requests if you want to use **Prepare release**. The workflow does not approve/merge
   its own PR. No personal access token or provider API keys are required.
3. Optionally enable GitHub private vulnerability reporting to use the route in SECURITY.md.
4. Run **Actions → Release → Run workflow** from the default branch. For the initial
   source this publishes **v0.4.0** after tests/build/smoke checks pass.

## Subsequent versions

1. Run **Prepare release** from the default branch. Choose `patch`, `minor` or `major`.
2. The action updates `pyproject.toml`, `src/anyllama/__init__.py` and the local package
   version in `uv.lock`, leaving dependency versions/hashes unchanged. It opens a version
   PR and explicitly starts CI on its branch. GitHub can also show an approval-required
   PR workflow run for bot-created PRs; approve it if your branch rules require that run.
3. Review the PR, add human-facing changelog notes if needed and merge after checks pass.
4. Run **Release** from the default branch. Alternatively, push a matching `vMAJOR.MINOR.PATCH`
   tag yourself; the tag triggers the same pipeline. A mismatching tag fails before publication.

For a local version bump:

```powershell
uv run python scripts/release_version.py bump --part patch
uv run python scripts/release_version.py check
uv lock --check
```

GitHub Actions using the built-in token do not recursively trigger ordinary push workflows.
The explicit CI dispatch avoids requiring a separate PAT for version PRs. See GitHub's
[workflow triggering rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

## What Release publishes

- Windows x64 `AnyLlama.exe`, built with its own Python runtime by PyInstaller.
- `AnyLlama-<version>-win-x64.zip`, including the EXE, MIT license, third-party notices
  and user/validation documentation.
- `SHA256SUMS.txt`, third-party notices and a runner smoke-check `validation.json`.

Only the publishing job has release write permissions. Builds run with read-only tokens.
Third-party Actions are pinned to full commit SHAs and Dependabot checks them weekly.
The publishing job verifies checksums and any existing tag's source commit, creates a
draft, uploads assets and then publishes it. It refuses to overwrite an existing release.
If a run leaves a draft after a failure, inspect it in GitHub; remove only that incomplete
draft before retrying, or finish it manually. Do not replace a published version's files.

The EXE is currently unsigned. The hosted runner smoke test skips the interactive tray
and real Windows Credential Manager checks, because a service session is not a desktop
acceptance environment. These skips are explicit in `validation.json`. Test those locally
with `uv run python scripts/verify_exe.py` before a release when changing desktop behavior.
GitHub-hosted workflow execution is only confirmed after the first push/run; local checks
and actionlint do not prove the hosted environment has executed successfully.

## Update checks in the application

AnyLlama reads the public **latest stable release** metadata from this repository. It sends
no provider API key, prompt, model selection, machine identifier or conversation data.
GitHub sees the ordinary network request and application User-Agent. Successful checks
are cached for 24 hours; failures are cached for an hour. Manual checks have a one-minute
cooldown. Checks run in the background and do not block generation.

Use **Settings → Updates** for a manual check or to disable automatic checks. A newer,
published stable release is advertised only when its matching Windows ZIP is uploaded.
The app constructs a GitHub release link itself; it never executes release notes, downloads
an executable or installs an update. Prereleases, drafts and malformed/incomplete metadata
are rejected. Until the first release exists, it reports that no stable release was found.

To install, exit AnyLlama through its tray menu, replace/use the new EXE and keep the same
data directory. Providers, model selections, keys and activity stay local. Starting a
second EXE while the old one is running opens that existing instance.
