# Working on this repository

This is public application source. Personal knowledge, source email/Slack material,
credentials, library exports, runtime history and deployment tooling belong outside
the repository. Read `security/README.md` before committing.

## Concurrent contributions

- Give each task its own Git worktree and `codex/<task>` branch based on current
  `origin/main`. Never share an index or working directory between active tasks.
- Keep PRs focused. Coordinate changes to shared interfaces and dependency locks.
- Do not switch branches, clean files, stop services or alter work in another task's
  checkout. Inspect existing changes before editing.
- Set up the privacy hooks in every worktree. Git configuration may be shared
  between worktrees; do not disable or bypass hooks.
- Push task branches and open PRs. Do not push directly to `main`, change repository
  permissions, bypass branch protection or merge your own work without owner direction.
- Before merging, incorporate latest `main` and obtain a passing `local/ci` status
  for the exact PR head. Only the owner operates the separate local controller.

## Checks and release

- Run the relevant Python tests and `npm ci && npm run build` in `frontend` for
  frontend changes. For durable execution changes, run both real Temporal recovery
  tests against an isolated development server, never the serving server.
- Use synthetic data and fake models in tests. Do not require an API key or send
  personal knowledge to a provider for verification.
- Local CI/CD is intentionally separate from this repository. Do not add GitHub
  Actions runners, deployment webhooks, production credentials or automated execution
  of public PRs. See `docs/contributing-and-releases.md`.
- Preserve the persistent application volume and existing workflow compatibility.
  Flag database migrations, workflow replay risks and dependency changes in the PR.
- Publishing code does not publish knowledge. Never bundle a local brain or copy
  runtime databases into a build. Knowledge publication requires owner review.
