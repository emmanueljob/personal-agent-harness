# Publishing source without personal data

Run `python3 scripts/setup_git_safety.py` after cloning. It downloads Gitleaks 8.30.1 from its official release, verifies the platform-specific SHA-256, and configures this checkout's `core.hooksPath` to `.githooks`. There are no scanner downloads during a commit or push. Missing scanners or scan errors block the operation.

The pre-commit hook inspects staged Git objects. The pre-push hook inspects **all history reachable from each ref being pushed**, including older versions of deleted files, commit messages, and annotated tags. It applies to every remote, including `origin`. It does not rely on `.gitignore` or only inspect the current working tree. Ref deletions send no file content and are allowed.

Checks reject runtime/dependency/data directories, environment files except the empty template, résumé documents, database files, unreviewed binary/unknown file types, symlinks and submodules. Source files are capped at 2 MB per object and 64 MB per scan. Adding a new public asset type requires a deliberate policy change and review; do not disable the guard to upload an asset.

On the owner's computer, the guard also compares content with local credential values and hashes of twelve-word passages from the private/raw brain. These references are read locally; neither their text nor fingerprints are uploaded or committed. Email addresses found in those references are also blocked. Matching content and scanner diagnostics are suppressed. Renamed/copied passages can be detected, but paraphrases, encodings and short personal details may evade these fingerprints. Text references over 2 MB block publication pending review.

The owner's checkout sets `git config --local privacy.requireLocalKnowledge true`, so missing private references block publication instead of silently skipping that comparison. Set this on other owner checkouts that should require the private brain. It is not enabled in CI or clean source-only clones.

Gitleaks uses its default credential rules through the explicit `security/gitleaks.toml` configuration. Inline `gitleaks:allow` comments and project ignore files cannot suppress this invocation. The separate owner-operated local CI controller repeats full-history checks with a trusted copy of the scanner and the owner's local private references; only its pass/fail status is published to GitHub. See [contributions and releases](../docs/contributing-and-releases.md).

No scanner proves the absence of all sensitive or proprietary information. Review the source and documentation before publishing. Git hooks can be bypassed by `--no-verify`, changed local configuration or altered scripts; they are an accidental-leak guard, not a security boundary against a malicious committer. Hooks are not automatically enabled on other clones. Local PR checks run after upload, so they are a backstop rather than a substitute for pre-push checks.

The reviewed text source patch at
`integrations/whatsapp-muse/companion.patch` is allowed at that exact path.
It receives the same credential and private-knowledge scans as source code.
Other patch exports remain blocked as unreviewed file types.

Manual commands:

```sh
python3 scripts/privacy_guard.py --staged
python3 scripts/privacy_guard.py --all
```

Keep actual `.env`, profiles, voice notes, library exports, email/Slack material, downloaded covers/spines and runtime databases outside Git. `.env.example` contains blank credentials only. Use a GitHub noreply email for commit metadata when personal contact details should stay private.

The owner-supplied synthetic Alpine landscape at
`frontend/public/images/alpine-panorama.jpeg` was visually reviewed for publication.
The guard permits only its exact SHA-256 in `PUBLIC_ASSETS`; other images and
replacements remain blocked until separately reviewed.
