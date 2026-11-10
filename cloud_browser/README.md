# Private cloud LinkedIn capture

A separate container runs stock Google Chrome on Xvfb/Openbox with the Chrome
sandbox enabled. No Playwright/Patchright runtime, headless mode, automation
flags, fingerprint overrides, CAPTCHA solvers, or proxy changes are used. The
owner controls the native desktop through noVNC inside admin.

The desktop's VNC and browser debugging ports bind to loopback only. An owner
POST creates a hashed, 60-second, single-use desktop ticket. The WebSocket
requires the configured same-origin Origin plus that ticket in a subprotocol;
credentials never appear in query strings or browser storage. The authenticated
app-to-worker bridge alone can reach the desktop relay. Active views reconnect
with fresh tickets and close after 30 minutes. Closing the view leaves capture
running. Browser/desktop subprocesses receive a minimal environment with no CRM
or gateway bearer tokens. Text fallback travels over stdin and a short-lived
private clipboard, never process arguments or saved screenshots.

The owner signs in manually before starting sync. Once enabled, the capture
service reads only LinkedIn-domain cookies from the worker's dedicated profile
through its private local session endpoint. Other account cookies are excluded.
The Chrome desktop is not controlled by an automation framework. Normal human
verification remains required by LinkedIn; cloud browser acceptance and live sync
must be tested with the owner's account before declaring the connection working.

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
this app. It does not have an official LinkedIn messaging API grant. The capture service
implements no message-send or mark-read methods. The owner can take normal actions
directly in the native Chrome desktop; those actions are separate from read-only sync.
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

The main database changes are additive (`crm_linkedin_capture` and single-use `crm_desktop_tickets`). Existing Gmail and
Temporal workflow schemas remain compatible; this feature adds no new Temporal
workflow history. Worker dependencies and durable storage are independent of the
serving app's volume.
