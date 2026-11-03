# Contributions and local releases

The source is public; repository write access remains with the owner. Forks and
pull requests do not grant push access. Contributions use individual worktrees and
`codex/` branches, with focused PRs into `main`.

CI/CD runs on the owner's Mac, in a private controller directory outside this
repository. There are no GitHub Actions jobs, self-hosted runners or deployment
webhooks. The controller reports only a `local/ci` result and a short description
to GitHub. Test logs, private fingerprint references and deployment credentials
remain local.

The owner's commands are:

```sh
portfolio check --pr 123 --reviewed-code
portfolio release
portfolio status
portfolio smoke
```

`check` fetches the exact reviewed PR head into an isolated checkout. It uses an
owner-maintained privacy scanner on the full reachable history, installs locked
dependencies, runs Python tests including two real Temporal crash/recovery tests
on a temporary server, and builds the frontend. Tests use temporary application
data and fake inference. Both Temporal tests must execute rather than skip.

Review the code before using `--reviewed-code`: a fresh worktree and stripped
environment are not a security sandbox. Tests and package code still execute as
the local OS user. Public PRs never trigger execution automatically.

`main` requires a PR and a passing `local/ci` status against current `main`, with
force pushes and deletion disabled and protection applying to administrators.
The owner reviews and merges; a separate mandatory GitHub approval is not required
because the same account may author the PR. GitHub credentials with sufficient
permissions can still submit statuses or change settings; this is a trusted-owner
workflow, not isolation from a malicious local agent using the owner's account.

A local launch agent checks for new `main` commits every two minutes while the Mac
is awake and connected. Each new commit is checked again before deployment. Only
one check or release runs at a time. If `main` advances during checks, deployment
stops; the next poll checks the new commit. A failed attempt is recorded and not
retried endlessly; `portfolio release` explicitly retries current `main`.

Deployments build the exact checked source on Fly and preserve the mounted volume.
They verify live HTTPS, health, public content, runtime configuration and owner
endpoint isolation without making a paid model call. Code deploys never export
knowledge or copy the owner's local state. Knowledge publication remains separate.
The single-Machine PoC restarts during deployment; do not expect zero downtime.

Release records and logs live only in the private controller directory. In a failed
deployment, inspect them before retrying. A previous image can be redeployed, but
image rollback does not undo database/schema changes. Preserve compatibility with
in-flight Temporal workflows and plan data migrations before merging them.

The deployed site keeps running when the Mac is offline; local checks and new
deployments wait until it returns. Changes to the private controller or its trusted
scanner must be reviewed separately from application PRs.
