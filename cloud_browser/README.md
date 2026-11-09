# Private cloud LinkedIn capture

A separate container runs Chromium through the open-source LinkedIn MCP browser
core. The main app provides an owner-authenticated screenshot/control view. No CDP,
VNC, stock MCP HTTP server, or unauthenticated browser port is exposed publicly.
The private harness tools can inspect/pause/resume capture; credentials are entered
by the owner in admin, not handed to an LLM.

## Data flow and recovery

LinkedIn realtime messages trigger durable thread reads. Initial connection and
every reconnect also reconcile inbox conversations and message history to repair
missed events. Inbox discovery includes message requests; it is not restricted to
the Focused inbox. Archive/spam completeness is not asserted. Normalized messages
are committed to a SQLite outbox before a history page advances. The worker keeps
retrying delivery until the app acknowledges the original message ID after saving
it. CRM receipt/version IDs deduplicate retransmissions and preserve owner edits.
A second account cannot silently mix into the pinned connection.

Transport keep-alives maintain the event stream; they do not query the inbox and
are not displayed as evidence of successful capture. UI status shows actual event,
read and saved times plus unfinished queue counts. Retries use exponential backoff
without a fixed attempt limit. Invalid schemas remain unfinished with a visible
attention state. Chrome termination restarts the container, reopens the persistent
profile and requeues interrupted work. LinkedIn verification still needs the owner.

The integration uses LinkedIn's web protocol, which can change independently of
this app. It does not have an official LinkedIn messaging API grant. No message
send or mark-read methods are implemented; browser Voyager API writes are blocked.
Message bodies remain untrusted evidence, never agent instructions. Attachments
are represented by available text/fallbacks, not downloaded. Capture is plain text.

## Configuration

Main app server-only environment:

- `CLOUD_BROWSER_URL`: private worker address, never supplied by a browser request.
- `CLOUD_BROWSER_TOKEN`: independent browser gateway bearer credential.
- `LINKEDIN_CAPTURE_TOKEN`: independent capture-only bearer credential.

Worker server-only environment:

- `BROWSER_WORKER_TOKEN`: matches the gateway credential.
- `CRM_CAPTURE_URL`: HTTPS `/api/hooks/linkedin` receiver on the main app.
- `CRM_CAPTURE_TOKEN`: matches the capture credential.
- `BROWSER_DATA_DIR`: mounted private persistent directory (default `/data/browser`).

Actual hosting configuration, secret files, browser profile, SQLite databases,
logs and deployment scripts stay outside this public repository. Keep exactly one
worker per volume; a filesystem lock prevents accidental concurrent profile use.
The image starts Chromium under a dedicated unprivileged UID. The private volume
contains session cookies and message history and needs the same protection as a
mailbox. Do not place it in a source checkout or a build context.

The worker dependencies are isolated and locked separately from the main harness.
Build using `cloud_browser/Dockerfile.txt` with the repository root as context; the
source allowlist in `.dockerignore` excludes local worker environments and runtime
data. No production deployment configuration or credentials are bundled here.

## Validation

`tests/test_cloud_browser.py` uses synthetic provider payloads, fake reads, and a
temporary volume. It covers stable identifiers, sender-based direction, exact
profile matching, restart recovery, replay-safe outbox storage, schema failures,
account pinning, authentication, and public isolation. It does not contact LinkedIn
or use personal browser sessions. Full harness checks and the frontend build are
also required. A real account is connected only through the private admin view;
initial historical capture and a subsequent actual event must be verified
separately before claiming end-to-end live sync.

The main database change is additive (`crm_linkedin_capture`). Existing Gmail and
Temporal workflow schemas remain compatible; this feature adds no new Temporal
workflow history. Worker dependencies and durable storage are independent of the
serving app's volume.
