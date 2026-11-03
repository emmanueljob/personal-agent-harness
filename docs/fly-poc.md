# Single-Machine Fly PoC

One x86-64 Machine runs FastAPI, the Deep Agents worker, and Temporal CLI's persistent development server. This is the owner's explicit PoC choice, not a production Temporal cluster. All three share `/data`; Temporal API/UI bind only to loopback. Keep one Machine and disable autostop so queued work and routines continue without web traffic. Deploys and host failure can interrupt the site.

The Docker build uses a source allowlist. It excludes Git, `.env`, runtime tools, all knowledge files, model weights, local databases and conversation history. The image contains code and the built frontend only. Privacy hooks explicitly recognize `Dockerfile` and `.dockerignore` as reviewed configuration text.

`PUBLIC_ONLY=true` disables owner routes even if an owner token is accidentally configured. Browser conversations are bound to a random Secure/HttpOnly/SameSite cookie; a thread ID alone does not grant access. Queue admission has visitor/IP/global limits, a bounded prompt length and pending queue. Model Activities have a separate global daily call counter, and public OpenAI calls have no SDK retries and a bounded output token budget. These are demo usage limits, not a guaranteed dollar spending cap. Provider billing controls still apply.

The cloud PoC reads the same approved canonical public knowledge through the file provider. The local GBrain configuration and indexes are untouched. The cloud starts with fresh chat state. Private brain, raw source material, local chats and the local owner token are never deployed. Hybrid inference uses the authenticated outbound Mac worker when its heartbeat is current and OpenAI for new turns while the Mac is offline. See [Mac model bridge](mac-model-bridge.md).

## Prepare

1. Run the test suite and frontend build.
2. Review the Docker context and the staged-source privacy scan.
3. Export published content separately, outside Git:

```sh
.venv/bin/python scripts/export_public_brain.py --brain-root /absolute/path/to/brain --output /absolute/path/outside/repository/public-brain.tgz
```

The exporter includes the public profile/voice/portfolio/shelf, wiki notes, active explicitly public podcast references, and assets belonging to currently published books. It rejects symlinks and excludes unrelated/unpublished cached assets. Its tar contains a hash manifest; no runtime database is copied.

## Deploy

Authenticate the official Fly CLI, choose the actual available app name and adjust `fly.toml`, then create one 10 GB `portfolio_data` volume in the chosen region. Install only the OpenAI API key as a Fly secret via stdin; do not pass it in shell arguments or copy `.env`. Use a remote build when local Docker is unavailable. Deploy with a single Machine (`--ha=false`), transfer the exported knowledge bundle over authenticated SFTP, verify its checksum, and extract into `/data` only after inspecting the manifest paths. Restart the Machine if needed. Use `/healthz` for process/service health and separately verify published content and an actual agent turn.

The initial deployment uses Fly's trial credit. Continuing after trial requires the owner to configure billing and approve ongoing cost. OpenAI API credits are separate from Fly and ChatGPT subscriptions.

For DNS, run `fly certs add <actual-hostname>` and use its exact CNAME and verification records in the registrar. Preserve MX and all Google service records. Verify authoritative DNS, certificate issuance and HTTPS before claiming the custom hostname is live.

## Restart, backup and restore

`serve_poc.py` starts Temporal first, then the worker and web server. Any child failure exits the supervisor so Fly can restart the Machine. Shutdown stops web and worker before Temporal. SQLite files, accepted turns and Temporal history remain on the persistent volume; rebuilding an image does not preserve its root filesystem.

Back up the public knowledge plus `state/harness.sqlite`, `state/temporal.sqlite` and legacy `state/checkpoints.sqlite` together using a consistent SQLite backup or a stopped Machine. Fly volume snapshots are an additional recovery option, not the only backup. Keep backups outside the same Machine/volume. Do not run replicas against divergent copies of these SQLite files. Move to shared application storage and a production Temporal service before increasing availability or distributing workers.
