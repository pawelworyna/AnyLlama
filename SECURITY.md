# Security

AnyLlama is a single-user, loopback-only desktop proxy. Other processes running as the
same user are trusted. Do not expose its inference/admin server to a network.

Report a suspected vulnerability through GitHub's private vulnerability reporting feature
if enabled for this repository. Do not put keys, prompts, private source code or bootstrap
dashboard links into public issues. If private reporting is unavailable, ask the maintainer
for a private channel without posting exploit details publicly.

Releases are built by GitHub Actions and include SHA-256 checksums. They are currently
unsigned Windows executables; checksums verify file integrity, not an independent signer.
Update checks read public release metadata from `pawelworyna/AnyLlama`; installation is manual.
No downloaded executable is run by the application. See [the review](docs/REVIEW.md)
for implemented controls and remaining limitations.
